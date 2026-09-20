"""配置中心仓储：策略/因子定义与**版本化参数配置**、数据源注册表与健康度。

版本化生命周期（spec「配置变更热生效且可回滚」）：

- 三态 ``draft`` / ``active`` / ``archived``；同一 owner（策略/因子）**至多一行 active**。
- :meth:`VersionedConfigRepository.activate` 在**单个事务**内先把该 owner 的全部版本置
  ``archived``，再把目标版本置 ``active``，从而保证任意时刻恰有一个 active。
- 回滚 = 以历史版本内容**新建**一个 active 版本（:meth:`VersionedConfigRepository.rollback`），
  历史记录只读不可变，便于审计与对比。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Generic, TypeVar

from sqlalchemy import func, select, update

from app.core.errors import NotFoundError
from app.models.config import (
    DatasourceHealth,
    DatasourceRegistry,
    FactorConfig,
    FactorDef,
    StrategyConfig,
    StrategyDef,
)
from app.repositories.base import BaseRepository

ModelT = TypeVar("ModelT")

STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class ParamDiff:
    """两个参数版本之间的参数级差异。

    Attributes:
        added: 仅存在于新版本（``v2``）的参数。
        removed: 仅存在于旧版本（``v1``）的参数。
        changed: 两侧都存在但取值不同的参数，值为 ``(旧值, 新值)``。
    """

    added: dict[str, Any]
    removed: dict[str, Any]
    changed: dict[str, tuple[Any, Any]]

    def is_empty(self) -> bool:
        """两侧是否完全一致。"""
        return not self.added and not self.removed and not self.changed


class VersionedConfigRepository(BaseRepository, Generic[ModelT]):
    """``draft`` / ``active`` / ``archived`` 版本化配置仓储的通用基类。

    子类须声明 :attr:`model`（ORM 模型）与 :attr:`owner_column`
    （归属列名，如 ``strategy_id`` / ``factor_id``）。
    """

    model: ClassVar[type[Any]]
    owner_column: ClassVar[str]

    def _col(self, name: str) -> Any:
        """取模型上名为 ``name`` 的列（供动态构造查询条件）。"""
        return getattr(self.model, name)

    def _owner_attr(self) -> Any:
        """归属列（owner 外键列）。"""
        return self._col(self.owner_column)

    async def _next_version(self, owner_id: str) -> int:
        """下一个版本号 = 当前最大版本 + 1。"""
        stmt = select(func.max(self._col("version"))).where(self._owner_attr() == owner_id)
        current = await self.session.scalar(stmt)
        return int(current or 0) + 1

    async def create_draft(
        self,
        owner_id: str,
        params: dict[str, Any],
        note: str | None = None,
        *,
        created_by: str | None = None,
    ) -> ModelT:
        """以 ``params`` 新建一个 ``draft`` 版本并返回该行。"""
        version = await self._next_version(owner_id)
        row: Any = self.model(
            **{
                self.owner_column: owner_id,
                "version": version,
                "status": STATUS_DRAFT,
                "params": params,
                "note": note,
                "created_by": created_by,
            }
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def activate(self, owner_id: str, version: int) -> ModelT:
        """把 ``version`` 置为 active，其余版本置 archived（单事务内原子完成）。

        Raises:
            NotFoundError: 目标版本不存在。
        """
        target: Any = await self._require_version(owner_id, version)
        await self.session.execute(
            update(self.model)
            .where(self._owner_attr() == owner_id)
            .values(status=STATUS_ARCHIVED)
            .execution_options(synchronize_session="fetch")
        )
        target.status = STATUS_ACTIVE
        await self.session.flush()
        return target

    async def get_active(self, owner_id: str) -> ModelT | None:
        """取该 owner 的 active 版本，无则返回 ``None``。"""
        stmt = select(self.model).where(
            self._owner_attr() == owner_id, self._col("status") == STATUS_ACTIVE
        )
        return await self.session.scalar(stmt)

    async def rollback(self, owner_id: str, version: int) -> ModelT:
        """回滚：以历史版本 ``version`` 的内容**新建**一个 active 版本并返回。

        Raises:
            NotFoundError: 目标版本不存在。
        """
        source: Any = await self._require_version(owner_id, version)
        draft: Any = await self.create_draft(
            owner_id,
            dict(source.params),
            f"回滚至版本 {version}",
        )
        return await self.activate(owner_id, int(draft.version))

    async def get_version(self, owner_id: str, version: int) -> ModelT | None:
        """按版本号取单行，不存在返回 ``None``。"""
        stmt = select(self.model).where(
            self._owner_attr() == owner_id, self._col("version") == version
        )
        return await self.session.scalar(stmt)

    async def list_versions(self, owner_id: str, limit: int = 50) -> list[ModelT]:
        """按版本号倒序列出版本。"""
        stmt = (
            select(self.model)
            .where(self._owner_attr() == owner_id)
            .order_by(self._col("version").desc())
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def history(self, owner_id: str, limit: int = 50) -> list[ModelT]:
        """版本列表（供前端版本列表 UI），等价于 :meth:`list_versions`。"""
        return await self.list_versions(owner_id, limit)

    async def diff_versions(self, owner_id: str, v1: int, v2: int) -> ParamDiff:
        """比较两个版本的参数差异。

        Raises:
            NotFoundError: 任一版本不存在。
        """
        left: Any = await self._require_version(owner_id, v1)
        right: Any = await self._require_version(owner_id, v2)
        old: dict[str, Any] = dict(left.params)
        new: dict[str, Any] = dict(right.params)
        added = {key: new[key] for key in new if key not in old}
        removed = {key: old[key] for key in old if key not in new}
        changed = {key: (old[key], new[key]) for key in old if key in new and old[key] != new[key]}
        return ParamDiff(added=added, removed=removed, changed=changed)

    async def _require_version(self, owner_id: str, version: int) -> ModelT:
        """取版本行，不存在则抛 :class:`NotFoundError`。"""
        row = await self.get_version(owner_id, version)
        if row is None:
            raise NotFoundError(
                f"{self.owner_column}={owner_id} 的版本 {version} 不存在",
                detail={"owner": owner_id, "version": version},
            )
        return row


class StrategyConfigRepository(VersionedConfigRepository[StrategyConfig]):
    """策略参数配置版本仓储。"""

    model: ClassVar[type[Any]] = StrategyConfig
    owner_column: ClassVar[str] = "strategy_id"


class FactorConfigRepository(VersionedConfigRepository[FactorConfig]):
    """因子参数配置版本仓储。"""

    model: ClassVar[type[Any]] = FactorConfig
    owner_column: ClassVar[str] = "factor_id"


class StrategyDefRepository(BaseRepository):
    """策略定义（注册表静态声明）仓储。"""

    async def upsert(
        self,
        strategy_id: str,
        label: str,
        version: str,
        params_schema: dict[str, Any],
        gate_matrix: dict[str, Any],
        *,
        description: str | None = None,
        enabled: bool = True,
    ) -> StrategyDef | None:
        """幂等写入策略定义（含参数 schema 与门控矩阵），返回写入后的行。"""
        await self.bulk_upsert(
            StrategyDef,
            [
                {
                    "strategy_id": strategy_id,
                    "label": label,
                    "version": version,
                    "enabled": enabled,
                    "description": description,
                    "params_schema": params_schema,
                    "gate_matrix": gate_matrix,
                }
            ],
            ("strategy_id",),
            ("label", "version", "enabled", "description", "params_schema", "gate_matrix"),
        )
        return await self.get(strategy_id)

    async def get(self, strategy_id: str) -> StrategyDef | None:
        """按标识取策略定义。"""
        return await self.session.get(StrategyDef, strategy_id)

    async def list_all(self) -> list[StrategyDef]:  # type: ignore[override]
        """列出全部策略定义，按标识升序。"""
        stmt = select(StrategyDef).order_by(StrategyDef.strategy_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_enabled(self, strategy_id: str, enabled: bool) -> bool:
        """启用/停用策略，返回是否命中记录。"""
        result = await self.session.execute(
            update(StrategyDef)
            .where(StrategyDef.strategy_id == strategy_id)
            .values(enabled=enabled)
            .execution_options(synchronize_session=False)
        )
        await self.session.flush()
        return bool(getattr(result, "rowcount", 0))


class FactorDefRepository(BaseRepository):
    """因子定义（注册表静态声明）仓储。"""

    async def upsert(
        self,
        factor_id: str,
        label: str,
        category: str,
        params_schema: dict[str, Any],
        *,
        description: str | None = None,
        enabled: bool = True,
    ) -> FactorDef | None:
        """幂等写入因子定义，返回写入后的行。"""
        await self.bulk_upsert(
            FactorDef,
            [
                {
                    "factor_id": factor_id,
                    "label": label,
                    "category": category,
                    "description": description,
                    "params_schema": params_schema,
                    "enabled": enabled,
                }
            ],
            ("factor_id",),
            ("label", "category", "description", "params_schema", "enabled"),
        )
        return await self.get(factor_id)

    async def get(self, factor_id: str) -> FactorDef | None:
        """按标识取因子定义。"""
        return await self.session.get(FactorDef, factor_id)

    async def list_all(self) -> list[FactorDef]:  # type: ignore[override]
        """列出全部因子定义，按标识升序。"""
        stmt = select(FactorDef).order_by(FactorDef.factor_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_enabled(self, factor_id: str, enabled: bool) -> bool:
        """启用/停用因子，返回是否命中记录。"""
        result = await self.session.execute(
            update(FactorDef)
            .where(FactorDef.factor_id == factor_id)
            .values(enabled=enabled)
            .execution_options(synchronize_session=False)
        )
        await self.session.flush()
        return bool(getattr(result, "rowcount", 0))


class DatasourceRegistryRepository(BaseRepository):
    """数据源注册表仓储（能力清单、限频预算、主备优先级）。"""

    async def upsert(
        self,
        source_id: str,
        label: str,
        kind: str,
        capabilities: Sequence[str],
        *,
        rate_limit_per_min: int = 20,
        priority: int = 100,
        enabled: bool = True,
    ) -> DatasourceRegistry | None:
        """幂等写入数据源注册信息，返回写入后的行。"""
        await self.bulk_upsert(
            DatasourceRegistry,
            [
                {
                    "source_id": source_id,
                    "label": label,
                    "kind": kind,
                    "capabilities": list(capabilities),
                    "rate_limit_per_min": rate_limit_per_min,
                    "enabled": enabled,
                    "priority": priority,
                }
            ],
            ("source_id",),
            ("label", "kind", "capabilities", "rate_limit_per_min", "enabled", "priority"),
        )
        return await self.get(source_id)

    async def get(self, source_id: str) -> DatasourceRegistry | None:
        """按标识取数据源。"""
        return await self.session.get(DatasourceRegistry, source_id)

    async def list_all(self) -> list[DatasourceRegistry]:  # type: ignore[override]
        """列出全部数据源，按优先级、标识升序。"""
        stmt = select(DatasourceRegistry).order_by(
            DatasourceRegistry.priority, DatasourceRegistry.source_id
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_enabled(self, source_id: str, enabled: bool) -> bool:
        """启用/停用数据源，返回是否命中记录。"""
        return await self._update_field(source_id, enabled=enabled)

    async def set_priority(self, source_id: str, priority: int) -> bool:
        """调整数据源优先级（数值越小越优先），返回是否命中记录。"""
        return await self._update_field(source_id, priority=priority)

    async def _update_field(self, source_id: str, **values: Any) -> bool:
        """按主键更新单个数据源的部分字段。"""
        result = await self.session.execute(
            update(DatasourceRegistry)
            .where(DatasourceRegistry.source_id == source_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await self.session.flush()
        return bool(getattr(result, "rowcount", 0))


class DatasourceHealthRepository(BaseRepository):
    """数据源能力健康度仓储。"""

    async def record(
        self,
        source_id: str,
        capability: str,
        ok: bool,
        *,
        latency_ms: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> DatasourceHealth:
        """追加一条健康度探测记录并返回该行。"""
        row = DatasourceHealth(
            source_id=source_id,
            capability=capability,
            ok=ok,
            latency_ms=latency_ms,
            detail=detail,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def latest_by_source(self) -> list[DatasourceHealth]:
        """每个 ``(source_id, capability)`` 的最新一条探测记录。"""
        latest = (
            select(
                DatasourceHealth.source_id.label("source_id"),
                DatasourceHealth.capability.label("capability"),
                func.max(DatasourceHealth.checked_at).label("checked_at"),
            )
            .group_by(DatasourceHealth.source_id, DatasourceHealth.capability)
            .subquery()
        )
        stmt = (
            select(DatasourceHealth)
            .join(
                latest,
                (DatasourceHealth.source_id == latest.c.source_id)
                & (DatasourceHealth.capability == latest.c.capability)
                & (DatasourceHealth.checked_at == latest.c.checked_at),
            )
            .order_by(DatasourceHealth.source_id, DatasourceHealth.capability)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def history(
        self, source_id: str, capability: str, limit: int = 50
    ) -> list[DatasourceHealth]:
        """某源某能力的探测历史，按探测时间倒序。"""
        stmt = (
            select(DatasourceHealth)
            .where(
                DatasourceHealth.source_id == source_id,
                DatasourceHealth.capability == capability,
            )
            .order_by(DatasourceHealth.checked_at.desc())
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


__all__ = [
    "DatasourceHealthRepository",
    "DatasourceRegistryRepository",
    "FactorConfigRepository",
    "FactorDefRepository",
    "ParamDiff",
    "StrategyConfigRepository",
    "StrategyDefRepository",
    "VersionedConfigRepository",
]
