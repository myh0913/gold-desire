"""配置中心模型：策略定义/参数版本、因子定义/参数版本、数据源注册表与健康度。

对应 spec「因子注册表与配置中心」「策略插件框架」「可插拔数据源」：

- 参数以 JSON 存储（PG 落 JSONB），版本化三态 ``draft`` / ``active`` / ``archived``。
- 同一 ``(strategy_id, version)`` / ``(factor_id, version)`` 唯一，回滚 = 以历史内容新建版本。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JsonType, TimestampMixin


class StrategyDef(TimestampMixin, Base):
    """策略定义（注册表中的静态声明，参数 schema 与门控矩阵随策略自身声明）。"""

    __tablename__ = "strategy_defs"

    strategy_id: Mapped[str] = mapped_column(String(64), primary_key=True, doc="策略唯一标识")
    label: Mapped[str] = mapped_column(String(128), nullable=False, doc="显示名")
    version: Mapped[str] = mapped_column(String(32), nullable=False, doc="策略代码版本号")
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(), doc="是否启用"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, doc="策略说明")
    params_schema: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, doc="参数 schema（key/类型/默认值/范围/单位/说明）"
    )
    gate_matrix: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, doc="情绪周期门控矩阵（周期态 → 是否允许 + 仓位系数）"
    )


class StrategyConfig(Base):
    """策略参数配置版本（``draft`` / ``active`` / ``archived``）。"""

    __tablename__ = "strategy_configs"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_configs_strategy_version"),
        Index("ix_strategy_configs_strategy_id_status", "strategy_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, doc="策略标识")
    version: Mapped[int] = mapped_column(Integer, nullable=False, doc="版本号（自增整数）")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", doc="状态：draft/active/archived"
    )
    params: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, doc="参数键值对")
    note: Mapped[str | None] = mapped_column(Text, nullable=True, doc="变更说明")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, doc="创建者用户名")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FactorDef(TimestampMixin, Base):
    """因子定义（注册表静态声明）。"""

    __tablename__ = "factor_defs"

    factor_id: Mapped[str] = mapped_column(String(64), primary_key=True, doc="因子唯一标识")
    label: Mapped[str] = mapped_column(String(128), nullable=False, doc="显示名")
    category: Mapped[str] = mapped_column(String(32), nullable=False, doc="因子类别")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, doc="因子说明")
    params_schema: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, doc="参数 schema（key/类型/默认值/范围/单位/说明）"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(), doc="是否启用"
    )


class FactorConfig(Base):
    """因子参数配置版本（``draft`` / ``active`` / ``archived``）。"""

    __tablename__ = "factor_configs"
    __table_args__ = (
        UniqueConstraint("factor_id", "version", name="uq_factor_configs_factor_version"),
        Index("ix_factor_configs_factor_id_status", "factor_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factor_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, doc="因子标识")
    version: Mapped[int] = mapped_column(Integer, nullable=False, doc="版本号（自增整数）")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", doc="状态：draft/active/archived"
    )
    params: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, doc="参数键值对")
    note: Mapped[str | None] = mapped_column(Text, nullable=True, doc="变更说明")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, doc="创建者用户名")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DatasourceRegistry(TimestampMixin, Base):
    """数据源注册表：能力清单、限频预算与主备优先级。"""

    __tablename__ = "datasource_registry"

    source_id: Mapped[str] = mapped_column(String(32), primary_key=True, doc="数据源唯一标识")
    label: Mapped[str] = mapped_column(String(64), nullable=False, doc="显示名")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, doc="类型：http/sdk/file 等")
    capabilities: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, doc="该源支持的能力清单"
    )
    rate_limit_per_min: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20, doc="每分钟调用上限"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(), doc="是否启用"
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, doc="优先级（数值越小越优先）"
    )


class DatasourceHealth(Base):
    """数据源能力健康度探测记录（采集侧周期性写入）。"""

    __tablename__ = "datasource_health"
    __table_args__ = (
        Index(
            "ix_datasource_health_source_capability_checked_at",
            "source_id",
            "capability",
            "checked_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(32), nullable=False, doc="数据源标识")
    capability: Mapped[str] = mapped_column(String(64), nullable=False, doc="能力标识")
    ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=false(), doc="是否可用"
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, doc="探测耗时（毫秒）")
    detail: Mapped[dict[str, Any] | None] = mapped_column(
        JsonType, nullable=True, doc="探测明细/错误信息"
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        server_default=func.now(),
        nullable=False,
        doc="探测时间",
    )


__all__ = [
    "DatasourceHealth",
    "DatasourceRegistry",
    "FactorConfig",
    "FactorDef",
    "StrategyConfig",
    "StrategyDef",
]
