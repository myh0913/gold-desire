"""数据源运维服务：注册表读、主备顺序调整、连通性探测、启停。

读方法只访问 DB（``datasource_registry`` / ``datasource_health``）+ 内存注册表；
写方法为 admin 操作，写审计并失效 ``datasource`` 缓存前缀。

连通性探测（:meth:`DatasourceService.ping`）**是唯一允许触达上游的入口**，且仅由
admin 显式触发——读路径绝不调用它。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.errors import NotFoundError, ValidationError
from app.datasources.contracts import CAPABILITY_CONTRACTS
from app.datasources.registry import get_provider, resolve_order, set_capability_order
from app.repositories import Repositories
from app.schemas.config import (
    DatasourceOut,
    DatasourcePingResponse,
    DatasourcePrefsResponse,
    DatasourcesResponse,
    DatasourceStateOut,
)
from app.services.cache_policy import CachePolicy, get_cache_policy, query_key

__all__ = ["DatasourceService"]


class DatasourceService:
    """数据源注册表与运维服务。"""

    def __init__(self, repos: Repositories, policy: CachePolicy | None = None) -> None:
        self._repos = repos
        self._policy = policy if policy is not None else get_cache_policy()

    async def _audit(
        self,
        actor: str | None,
        action: str,
        target: str | None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """写一条审计日志。"""
        await self._repos.audit_logs.record(
            actor=actor, action=action, target=target, detail=detail
        )

    # ------------------------------------------------------------------ 读

    async def datasources(self) -> DatasourcesResponse:
        """数据源注册表 + 能力取数顺序 + 最新健康度。"""
        key = query_key("datasource", {"view": "registry"})

        async def loader() -> DatasourcesResponse:
            rows = await self._repos.datasource_registry.list_all()
            health_rows = await self._repos.datasource_health.latest_by_source()
            health: dict[str, dict[str, dict[str, Any]]] = {}
            last_check: dict[str, Any] = {}
            for item in health_rows:
                health.setdefault(str(item.source_id), {})[str(item.capability)] = {
                    "ok": bool(item.ok),
                    "latency_ms": item.latency_ms,
                    "detail": item.detail,
                }
                previous = last_check.get(str(item.source_id))
                if previous is None or item.checked_at > previous:
                    last_check[str(item.source_id)] = item.checked_at

            items = [
                DatasourceOut(
                    source_id=str(row.source_id),
                    label=str(row.label),
                    kind=str(row.kind),
                    capabilities=[str(cap) for cap in row.capabilities],
                    rate_limit_per_min=int(row.rate_limit_per_min),
                    enabled=bool(row.enabled),
                    priority=int(row.priority),
                    health=health.get(str(row.source_id), {}),
                    last_check=last_check.get(str(row.source_id)),
                )
                for row in rows
            ]
            order = {cap: resolve_order(cap) for cap in sorted(CAPABILITY_CONTRACTS)}
            return DatasourcesResponse(items=items, capability_order=order)

        return await self._policy.get_or_load(
            "datasource", key, loader, DatasourcesResponse.model_validate
        )

    # ------------------------------------------------------------------ 写

    async def set_prefs(
        self, prefs: dict[str, list[str]], *, actor: str | None
    ) -> DatasourcePrefsResponse:
        """保存能力 → 有序源列表（主备切换），并持久化优先级。

        Raises:
            ValidationError: 能力未声明，或引用了未注册的数据源。
        """
        applied: dict[str, list[str]] = {}
        for capability, source_ids in prefs.items():
            if capability not in CAPABILITY_CONTRACTS:
                raise ValidationError(
                    f"未声明的能力：{capability}", detail={"capability": capability}
                )
            if not source_ids:
                raise ValidationError(
                    f"能力 {capability} 的源列表不得为空", detail={"capability": capability}
                )
            for source_id in source_ids:
                try:
                    get_provider(source_id)
                except Exception as exc:
                    raise ValidationError(
                        f"未注册的数据源：{source_id}",
                        detail={"capability": capability, "source_id": source_id},
                    ) from exc
            set_capability_order(capability, list(source_ids))
            for index, source_id in enumerate(source_ids):
                await self._repos.datasource_registry.set_priority(source_id, index)
            applied[capability] = list(source_ids)

        await self._audit(actor, "datasource_prefs_update", None, {"prefs": applied})
        await self._policy.invalidate("datasource")
        return DatasourcePrefsResponse(prefs=applied)

    async def ping(self, source_ids: list[str], *, actor: str | None) -> DatasourcePingResponse:
        """对指定数据源做连通性探测（缺省探测全部已注册源），并记录健康度。

        探测会对每个源的第一项能力发起一次真实取数；失败仅记录 ``ok=False``，
        不影响接口成功返回。
        """
        rows = await self._repos.datasource_registry.list_all()
        targets = source_ids or [str(row.source_id) for row in rows]
        if not targets:
            targets = sorted({cls.source_id for cls in _all_provider_classes()})

        results: dict[str, dict[str, Any]] = {}
        for source_id in targets:
            try:
                provider_cls = get_provider(source_id)
            except Exception as exc:
                raise NotFoundError(
                    f"未注册的数据源：{source_id}", detail={"source_id": source_id}
                ) from exc
            provider = provider_cls()
            capability = provider.capabilities[0] if provider.capabilities else ""
            results[source_id] = {}
            if not capability:
                results[source_id]["_"] = {"ok": False, "latency_ms": None, "error": "无能力"}
                continue
            started = time.perf_counter()
            try:
                await provider.acquire()
                await provider.fetch(capability, date=_today_iso())
                latency = int((time.perf_counter() - started) * 1000)
                results[source_id][capability] = {"ok": True, "latency_ms": latency, "error": None}
                await self._repos.datasource_health.record(
                    source_id, capability, True, latency_ms=latency
                )
            except Exception as exc:
                latency = int((time.perf_counter() - started) * 1000)
                message = f"{type(exc).__name__}: {exc}"
                results[source_id][capability] = {
                    "ok": False,
                    "latency_ms": latency,
                    "error": message,
                }
                await self._repos.datasource_health.record(
                    source_id, capability, False, latency_ms=latency, detail={"error": message}
                )

        await self._audit(actor, "datasource_ping", None, {"sources": targets})
        await self._policy.invalidate("datasource")
        return DatasourcePingResponse(results=results)

    async def set_enabled(
        self, source_id: str, enabled: bool, *, actor: str | None
    ) -> DatasourceStateOut:
        """启用/停用数据源。"""
        hit = await self._repos.datasource_registry.set_enabled(source_id, enabled)
        if not hit:
            raise NotFoundError(f"数据源不存在：{source_id}", detail={"source_id": source_id})
        await self._audit(
            actor, "datasource_enable" if enabled else "datasource_disable", source_id, None
        )
        await self._policy.invalidate("datasource")
        return DatasourceStateOut(source_id=source_id, enabled=enabled)


def _all_provider_classes() -> list[Any]:
    """全部已注册 provider 类（用于缺省探测全部）。"""
    from app.datasources.registry import all_providers

    return all_providers()


def _today_iso() -> str:
    """今天的 ISO 日期串（探测参数占位）。"""
    from datetime import date

    return date.today().isoformat()
