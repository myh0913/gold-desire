"""配置中心服务：策略/因子定义与版本化参数、因子有效性统计。

- 读方法（``strategies`` / ``factors`` / ``factor_effectiveness``）只访问 DB + 缓存；
- 写方法（保存并启用、回滚、启停）走既有**版本生命周期**仓储，写 :class:`AuditLog`
  并失效 ``config`` 缓存前缀（写后失效）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast

from app.core.errors import NotFoundError, ValidationError
from app.engine.dragon_samples import build_samples
from app.factors.base import all_factors, get_factor
from app.factors.registry import FactorRepos
from app.factors.registry import resolve_params as resolve_factor_params
from app.factors.stats import FactorSample, effectiveness, segment_cuts
from app.repositories import Repositories
from app.schemas.config import (
    BucketSegmentOut,
    BucketStatsOut,
    ConfigDiffOut,
    ConfigVersionOut,
    ConfigVersionsResponse,
    FactorEffectivenessResponse,
    FactorOut,
    FactorsResponse,
    StrategiesResponse,
    StrategyOut,
    StrategyStateOut,
)
from app.services.cache_policy import CachePolicy, get_cache_policy, query_key
from app.services.paging import clamp_limit
from app.strategies.protocol import StrategyRegistryError, StrategyRepos, resolve_params
from app.strategies.registry import get_strategy, set_enabled

__all__ = ["ConfigService"]

#: 版本历史返回条数上限（避免无界）。
VERSION_HISTORY_LIMIT = 50

#: 有效性统计默认/最大样本窗口（自然日）。
DEFAULT_EFFECTIVENESS_DAYS = 180
MAX_EFFECTIVENESS_DAYS = 400


def _iso(value: date | None) -> str | None:
    """日期转 ISO 串（供缓存键）。"""
    return value.isoformat() if value is not None else None


class ConfigService:
    """策略/因子配置与统计服务。"""

    def __init__(self, repos: Repositories, policy: CachePolicy | None = None) -> None:
        self._repos = repos
        self._policy = policy if policy is not None else get_cache_policy()

    # ------------------------------------------------------------------ 审计

    async def _audit(
        self, actor: str | None, action: str, target: str, detail: dict[str, Any] | None = None
    ) -> None:
        """写一条审计日志（写接口必备）。"""
        await self._repos.audit_logs.record(
            actor=actor, action=action, target=target, detail=detail
        )

    # ------------------------------------------------------------------ 策略读

    async def strategies(self) -> StrategiesResponse:
        """列出全部策略（定义 + schema + 生效参数 + 版本历史）。"""
        key = query_key("config", {"view": "strategies"})

        async def loader() -> StrategiesResponse:
            rows = await self._repos.strategy_defs.list_all()
            items: list[StrategyOut] = []
            for row in rows:
                items.append(await self._strategy_out(str(row.strategy_id)))
            return StrategiesResponse(items=items)

        return await self._policy.get_or_load(
            "config", key, loader, StrategiesResponse.model_validate
        )

    async def strategy(self, strategy_id: str) -> StrategyOut:
        """取单个策略（不存在 404）。"""
        key = query_key("config", {"view": "strategy", "id": strategy_id})

        async def loader() -> StrategyOut:
            return await self._strategy_out(strategy_id)

        return await self._policy.get_or_load("config", key, loader, StrategyOut.model_validate)

    async def _strategy_out(self, strategy_id: str) -> StrategyOut:
        """组装策略响应（DB 定义优先，代码注册表兜底）。"""
        defn = await self._repos.strategy_defs.get(strategy_id)
        registered = None
        try:
            registered = get_strategy(strategy_id)
        except StrategyRegistryError:
            registered = None
        if defn is None and registered is None:
            raise NotFoundError(f"策略不存在：{strategy_id}", detail={"strategy_id": strategy_id})

        phases: list[str] = []
        if defn is not None:
            label = str(defn.label)
            version = str(defn.version)
            description = defn.description
            enabled = bool(defn.enabled)
            params_schema = dict(defn.params_schema)
            gate_matrix = dict(defn.gate_matrix)
            if registered is not None:
                phases = sorted(phase.value for phase in registered.phases)
        else:
            instance = registered()  # type: ignore[misc]
            label = instance.label
            version = instance.version
            description = instance.description
            enabled = True
            params_schema = instance.params_schema_dict()
            gate_matrix = instance.gate_matrix_dict()
            phases = sorted(phase.value for phase in registered.phases)  # type: ignore[union-attr]

        active = await self._repos.strategy_configs.get_active(strategy_id)
        if registered is not None:
            active_params = dict(
                (await resolve_params(registered, cast("StrategyRepos", self._repos))).params
            )
        else:
            active_params = dict(active.params) if active is not None else {}
        versions = await self._repos.strategy_configs.list_versions(
            strategy_id, VERSION_HISTORY_LIMIT
        )
        return StrategyOut(
            strategy_id=strategy_id,
            label=label,
            version=version,
            description=description,
            enabled=enabled,
            phases=phases,
            gate_matrix=gate_matrix,
            params_schema=params_schema,
            active_version=int(active.version) if active is not None else None,
            active_params=active_params,
            versions=[self._version_out(row, strategy_id) for row in versions],
        )

    # ------------------------------------------------------------------ 因子读

    async def factors(self) -> FactorsResponse:
        """列出全部因子（定义 + schema + 生效参数）。"""
        key = query_key("config", {"view": "factors"})

        async def loader() -> FactorsResponse:
            rows = await self._repos.factor_defs.list_all()
            items: list[FactorOut] = []
            if rows:
                for row in rows:
                    items.append(await self._factor_out(str(row.factor_id)))
            else:  # DB 未同步时回退到代码注册表
                for schema in (cls().schema_dict() for cls in all_factors()):
                    items.append(await self._factor_out(str(schema["factor_id"])))
            return FactorsResponse(items=items)

        return await self._policy.get_or_load(
            "config", key, loader, FactorsResponse.model_validate
        )

    async def factor(self, factor_id: str) -> FactorOut:
        """取单个因子（不存在 404）。"""
        key = query_key("config", {"view": "factor", "id": factor_id})

        async def loader() -> FactorOut:
            return await self._factor_out(factor_id)

        return await self._policy.get_or_load("config", key, loader, FactorOut.model_validate)

    async def _factor_out(self, factor_id: str) -> FactorOut:
        """组装因子响应（DB 定义优先，代码注册表兜底）。"""
        defn = await self._repos.factor_defs.get(factor_id)
        registered = None
        try:
            registered = get_factor(factor_id)
        except Exception:  # FactorRegistryError
            registered = None
        if defn is None and registered is None:
            raise NotFoundError(f"因子不存在：{factor_id}", detail={"factor_id": factor_id})

        if defn is not None:
            label = str(defn.label)
            category = str(defn.category)
            description = defn.description
            enabled = bool(defn.enabled)
            params_schema = dict(defn.params_schema)
        else:
            schema = registered().schema_dict()  # type: ignore[misc]
            label = str(schema["label"])
            category = str(schema["category"])
            description = schema["description"]
            enabled = True
            params_schema = {"params": schema["params"], "buckets": schema["buckets"]}

        active = await self._repos.factor_configs.get_active(factor_id)
        if registered is not None:
            active_params = dict(
                (await resolve_factor_params(factor_id, cast("FactorRepos", self._repos))).params
            )
        else:
            active_params = dict(active.params) if active is not None else {}
        buckets = params_schema.get("buckets") or []
        return FactorOut(
            factor_id=factor_id,
            label=label,
            category=category,
            description=description,
            enabled=enabled,
            params_schema=params_schema,
            buckets=list(buckets),
            active_version=int(active.version) if active is not None else None,
            active_params=active_params,
        )

    # -------------------------------------------------------------- 因子有效性

    async def factor_effectiveness(
        self, factor_id: str, *, start: date | None, end: date | None, min_boards: int
    ) -> FactorEffectivenessResponse:
        """按档位 + A/B/C 三段输出因子有效性（**绝不只报聚合**）。

        前向收益口径：``D+1 开盘价买入 → D+2 收盘价了结`` 的毛收益
        （``t1.close / t.open - 1``，不含止损），与因子有效性评估的中性口径一致。
        """
        get_factor(factor_id)  # 未注册直接抛 FactorRegistryError
        window_end = end or date.today()
        window_start = start or (window_end - timedelta(days=DEFAULT_EFFECTIVENESS_DAYS))
        if window_start > window_end:
            raise ValidationError("统计起始日不得晚于结束日", code="invalid_range")
        earliest = window_end - timedelta(days=MAX_EFFECTIVENESS_DAYS)
        if window_start < earliest:
            window_start = earliest

        key = query_key(
            "config",
            {
                "view": "effectiveness",
                "factor": factor_id,
                "start": _iso(window_start),
                "end": _iso(window_end),
                "min_boards": min_boards,
            },
        )

        async def loader() -> FactorEffectivenessResponse:
            resolved = await resolve_factor_params(factor_id, cast("FactorRepos", self._repos))
            params = dict(resolved.params)
            instance = get_factor(factor_id)()
            samples = await build_samples(
                self._repos, window_start, window_end, min_boards=min_boards
            )
            factor_samples: list[FactorSample] = []
            for sample in samples:
                forward = _forward_return(sample)
                if forward is None:
                    continue
                result = instance.compute(sample.factor_context(), params)
                factor_samples.append(
                    FactorSample(
                        code=sample.code,
                        trade_date=sample.D,
                        value=result.value,
                        forward_return=forward,
                    )
                )
            cuts = segment_cuts([item.trade_date for item in factor_samples])
            buckets = effectiveness(factor_id, factor_samples, params, cuts=cuts)
            return FactorEffectivenessResponse(
                factor_id=factor_id,
                params_version=resolved.version,
                cuts=cuts,
                sample_count=len(factor_samples),
                buckets=[
                    BucketStatsOut(
                        bucket=item.bucket,
                        n=item.n,
                        mean_return=item.mean_return,
                        win_rate=item.win_rate,
                        segments={
                            name: BucketSegmentOut(
                                n=seg.n, mean_return=seg.mean_return, win_rate=seg.win_rate
                            )
                            for name, seg in item.segments.items()
                        },
                    )
                    for item in buckets
                ],
            )

        return await self._policy.get_or_load(
            "config", key, loader, FactorEffectivenessResponse.model_validate
        )

    # ------------------------------------------------------------------ 策略写

    async def save_strategy_config(
        self, strategy_id: str, params: dict[str, Any], *, note: str | None, actor: str | None
    ) -> ConfigVersionOut:
        """保存并启用策略参数（新建 draft → 置 active，历史版本归档）。"""
        await self._require_strategy(strategy_id)
        draft = await self._repos.strategy_configs.create_draft(
            strategy_id, params, note, created_by=actor
        )
        active = await self._repos.strategy_configs.activate(strategy_id, int(draft.version))
        await self._audit(
            actor,
            "strategy_config_save",
            strategy_id,
            {"version": int(active.version), "params": params},
        )
        await self._policy.invalidate("config")
        return self._version_out(active, strategy_id)

    async def strategy_versions(
        self, strategy_id: str, limit: int | None
    ) -> ConfigVersionsResponse:
        """策略参数版本历史。"""
        await self._require_strategy(strategy_id)
        rows = await self._repos.strategy_configs.list_versions(
            strategy_id, clamp_limit(limit, default=VERSION_HISTORY_LIMIT)
        )
        return ConfigVersionsResponse(
            owner_id=strategy_id, items=[self._version_out(row, strategy_id) for row in rows]
        )

    async def strategy_version_diff(
        self, strategy_id: str, from_version: int, to_version: int
    ) -> ConfigDiffOut:
        """比较两个策略参数版本。"""
        await self._require_strategy(strategy_id)
        diff = await self._repos.strategy_configs.diff_versions(
            strategy_id, from_version, to_version
        )
        return ConfigDiffOut(
            owner_id=strategy_id,
            from_version=from_version,
            to_version=to_version,
            added=diff.added,
            removed=diff.removed,
            changed=diff.changed,
        )

    async def rollback_strategy(
        self, strategy_id: str, version: int, *, actor: str | None
    ) -> ConfigVersionOut:
        """回滚策略参数到历史版本（以历史内容新建 active 版本）。"""
        await self._require_strategy(strategy_id)
        active = await self._repos.strategy_configs.rollback(strategy_id, version)
        await self._audit(
            actor,
            "strategy_config_rollback",
            strategy_id,
            {"from_version": version},
        )
        await self._policy.invalidate("config")
        return self._version_out(active, strategy_id)

    async def set_strategy_enabled(
        self, strategy_id: str, enabled: bool, *, actor: str | None
    ) -> StrategyStateOut:
        """启用/停用策略。"""
        try:
            await set_enabled(strategy_id, enabled, cast("StrategyRepos", self._repos))
        except StrategyRegistryError as exc:
            raise NotFoundError(str(strategy_id), detail={"strategy_id": strategy_id}) from exc
        await self._audit(
            actor, "strategy_enable" if enabled else "strategy_disable", strategy_id, None
        )
        await self._policy.invalidate("config")
        return StrategyStateOut(strategy_id=strategy_id, enabled=enabled)

    async def _require_strategy(self, strategy_id: str) -> None:
        """校验策略存在（DB 定义或代码注册表）。"""
        defn = await self._repos.strategy_defs.get(strategy_id)
        if defn is not None:
            return
        try:
            get_strategy(strategy_id)
        except StrategyRegistryError as exc:
            raise NotFoundError(str(strategy_id), detail={"strategy_id": strategy_id}) from exc

    # ------------------------------------------------------------------ 因子写

    async def save_factor_config(
        self, factor_id: str, params: dict[str, Any], *, note: str | None, actor: str | None
    ) -> ConfigVersionOut:
        """保存并启用因子参数（新建 draft → 置 active）。"""
        self._require_factor(factor_id)
        draft = await self._repos.factor_configs.create_draft(
            factor_id, params, note, created_by=actor
        )
        active = await self._repos.factor_configs.activate(factor_id, int(draft.version))
        await self._audit(
            actor,
            "factor_config_save",
            factor_id,
            {"version": int(active.version), "params": params},
        )
        await self._policy.invalidate("config")
        return self._version_out(active, factor_id)

    async def factor_versions(self, factor_id: str, limit: int | None) -> ConfigVersionsResponse:
        """因子参数版本历史。"""
        self._require_factor(factor_id)
        rows = await self._repos.factor_configs.list_versions(
            factor_id, clamp_limit(limit, default=VERSION_HISTORY_LIMIT)
        )
        return ConfigVersionsResponse(
            owner_id=factor_id, items=[self._version_out(row, factor_id) for row in rows]
        )

    async def factor_version_diff(
        self, factor_id: str, from_version: int, to_version: int
    ) -> ConfigDiffOut:
        """比较两个因子参数版本。"""
        self._require_factor(factor_id)
        diff = await self._repos.factor_configs.diff_versions(factor_id, from_version, to_version)
        return ConfigDiffOut(
            owner_id=factor_id,
            from_version=from_version,
            to_version=to_version,
            added=diff.added,
            removed=diff.removed,
            changed=diff.changed,
        )

    async def rollback_factor(
        self, factor_id: str, version: int, *, actor: str | None
    ) -> ConfigVersionOut:
        """回滚因子参数到历史版本。"""
        self._require_factor(factor_id)
        active = await self._repos.factor_configs.rollback(factor_id, version)
        await self._audit(actor, "factor_config_rollback", factor_id, {"from_version": version})
        await self._policy.invalidate("config")
        return self._version_out(active, factor_id)

    def _require_factor(self, factor_id: str) -> None:
        """校验因子存在（代码注册表为准）。"""
        try:
            get_factor(factor_id)
        except Exception as exc:  # FactorRegistryError
            raise NotFoundError(str(factor_id), detail={"factor_id": factor_id}) from exc

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _version_out(row: Any, owner_id: str) -> ConfigVersionOut:
        """把配置版本行转为响应模型。"""
        return ConfigVersionOut(
            owner_id=owner_id,
            version=int(row.version),
            status=str(row.status),
            params=dict(row.params),
            note=row.note,
            created_by=row.created_by,
            created_at=row.created_at,
        )


def _forward_return(sample: Any) -> float | None:
    """样本前向收益：``D+1 开盘价 → D+2 收盘价``；数据不足返回 ``None``。"""
    buy = sample.t.open
    sell = sample.t1.close
    if buy is None or sell is None or buy <= 0:
        return None
    return float(sell) / float(buy) - 1
