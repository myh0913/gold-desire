"""配置中心与运维域响应契约：策略/因子定义与版本、数据源注册表、采集任务。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BucketSegmentOut",
    "BucketStatsOut",
    "CapabilityHealthOut",
    "ConfigDiffOut",
    "ConfigVersionOut",
    "ConfigVersionsResponse",
    "DatasourceOut",
    "DatasourcePingRequest",
    "DatasourcePingResponse",
    "DatasourcePrefsResponse",
    "DatasourcePrefsUpdate",
    "DatasourceStateOut",
    "DatasourcesResponse",
    "FactorConfigUpdate",
    "FactorEffectivenessResponse",
    "FactorOut",
    "FactorsResponse",
    "IngestHealthResponse",
    "IngestJobOut",
    "IngestJobsResponse",
    "IngestTriggerOut",
    "IngestTriggerRequest",
    "StrategiesResponse",
    "StrategyConfigUpdate",
    "StrategyOut",
    "StrategyStateOut",
]


# --------------------------------------------------------------- 版本化配置


class ConfigVersionOut(BaseModel):
    """一个参数配置版本（``draft`` / ``active`` / ``archived``）。"""

    owner_id: str = Field(description="归属标识（策略或因子 id）")
    version: int
    status: str
    params: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None


class ConfigVersionsResponse(BaseModel):
    """某归属（策略/因子）的版本历史。"""

    owner_id: str
    items: list[ConfigVersionOut] = Field(default_factory=list)


class ConfigDiffOut(BaseModel):
    """两个版本之间的参数差异。"""

    owner_id: str
    from_version: int
    to_version: int
    added: dict[str, Any] = Field(default_factory=dict)
    removed: dict[str, Any] = Field(default_factory=dict)
    changed: dict[str, tuple[Any, Any]] = Field(default_factory=dict)


class StrategyConfigUpdate(BaseModel):
    """保存并启用策略参数（整包覆盖）。"""

    params: dict[str, Any] = Field(description="参数键值对（整包）")
    note: str | None = Field(default=None, max_length=255, description="变更说明")


class FactorConfigUpdate(BaseModel):
    """保存并启用因子参数（整包覆盖）。"""

    params: dict[str, Any] = Field(description="参数键值对（整包）")
    note: str | None = Field(default=None, max_length=255, description="变更说明")


# --------------------------------------------------------------- 策略 / 因子


class StrategyOut(BaseModel):
    """策略定义 + 参数 schema + 生效参数 + 版本历史。"""

    strategy_id: str
    label: str
    version: str
    description: str | None = None
    enabled: bool
    phases: list[str] = Field(default_factory=list)
    gate_matrix: dict[str, Any] = Field(default_factory=dict)
    params_schema: dict[str, Any] = Field(default_factory=dict)
    active_version: int | None = None
    active_params: dict[str, Any] = Field(default_factory=dict)
    versions: list[ConfigVersionOut] = Field(default_factory=list)


class StrategiesResponse(BaseModel):
    """策略列表。"""

    items: list[StrategyOut] = Field(default_factory=list)


class StrategyStateOut(BaseModel):
    """策略启停结果。"""

    strategy_id: str
    enabled: bool


class FactorOut(BaseModel):
    """因子定义 + 参数 schema + 生效参数。"""

    factor_id: str
    label: str
    category: str
    description: str | None = None
    enabled: bool
    params_schema: dict[str, Any] = Field(default_factory=dict)
    buckets: list[dict[str, Any]] = Field(default_factory=list)
    active_version: int | None = None
    active_params: dict[str, Any] = Field(default_factory=dict)


class FactorsResponse(BaseModel):
    """因子列表。"""

    items: list[FactorOut] = Field(default_factory=list)


class BucketSegmentOut(BaseModel):
    """单段（A/B/C）统计。"""

    n: int
    mean_return: float
    win_rate: float


class BucketStatsOut(BaseModel):
    """单档位统计（含 A/B/C 分段，绝不只报聚合）。"""

    bucket: str
    n: int
    mean_return: float
    win_rate: float
    segments: dict[str, BucketSegmentOut] = Field(default_factory=dict)


class FactorEffectivenessResponse(BaseModel):
    """因子有效性统计（按档位 + A/B/C 分段）。"""

    factor_id: str
    params_version: str
    cuts: tuple[date, date] | None = None
    sample_count: int
    buckets: list[BucketStatsOut] = Field(default_factory=list)


# --------------------------------------------------------------- 数据源 / 采集


class DatasourceOut(BaseModel):
    """数据源注册信息 + 最新健康度。"""

    source_id: str
    label: str
    kind: str
    capabilities: list[str] = Field(default_factory=list)
    rate_limit_per_min: int
    enabled: bool
    priority: int
    health: dict[str, dict[str, Any]] = Field(default_factory=dict)
    last_check: datetime | None = None


class DatasourcesResponse(BaseModel):
    """数据源注册表 + 能力取数顺序 + 健康度。"""

    items: list[DatasourceOut] = Field(default_factory=list)
    capability_order: dict[str, list[str]] = Field(default_factory=dict)


class DatasourcePrefsUpdate(BaseModel):
    """能力 → 有序源列表（主备切换）。"""

    prefs: dict[str, list[str]] = Field(
        default_factory=dict, description="能力名 → 有序源 id（主源在前）"
    )


class DatasourcePrefsResponse(BaseModel):
    """保存后的能力取数顺序。"""

    prefs: dict[str, list[str]] = Field(default_factory=dict)


class DatasourceStateOut(BaseModel):
    """数据源启停结果。"""

    source_id: str
    enabled: bool


class DatasourcePingRequest(BaseModel):
    """连通性探测请求：``sources`` 缺省探测全部已注册源。"""

    sources: list[str] = Field(default_factory=list, description="待探测数据源 id 列表")


class DatasourcePingResponse(BaseModel):
    """连通性探测结果：``source_id -> {capability: {ok, latency_ms, error}}``。"""

    results: dict[str, dict[str, Any]] = Field(default_factory=dict)


class IngestJobOut(BaseModel):
    """采集任务执行明细。"""

    model_config = ConfigDict(from_attributes=True)

    job_id: str
    capability: str
    source: str
    trade_date: date | None = None
    status: str
    rows: int
    attempts: int
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class IngestJobsResponse(BaseModel):
    """最近采集任务列表（条数受 ``limit`` 约束）。"""

    limit: int
    items: list[IngestJobOut] = Field(default_factory=list)


class CapabilityHealthOut(BaseModel):
    """单能力采集健康度。"""

    last_success: datetime | None = None
    last_failure: datetime | None = None
    consecutive_failures: int = 0


class IngestHealthResponse(BaseModel):
    """采集健康度汇总。"""

    since: datetime
    capabilities: dict[str, CapabilityHealthOut] = Field(default_factory=dict)


class IngestTriggerRequest(BaseModel):
    """手动触发采集请求。"""

    task: str = Field(min_length=1, max_length=64, description="采集任务名")
    trade_date: date | None = Field(default=None, description="目标交易日；缺省为今天")


class IngestTriggerOut(BaseModel):
    """手动触发一次采集的结果。"""

    task: str
    capability: str
    trade_date: date
    status: str
    rows: int
    source: str
    job_id: str
    error: str | None = None
    attempts: int
    duration_ms: int
