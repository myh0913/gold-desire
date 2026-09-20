"""因子注册表与配置中心。

分层：

- :mod:`app.factors.base` — 因子协议、档位定义、计算上下文与注册表；
- :mod:`app.factors.registry` — schema 导出、定义同步、参数解析（覆盖 > active 配置 > 默认）；
- :mod:`app.factors.builtin` — 首批内置因子（导入即注册）；
- :mod:`app.factors.cache` — 结果缓存（键含参数版本）；
- :mod:`app.factors.stats` — 因子有效性统计（含 A/B/C 分段）。

导入本包即完成内置因子注册。
"""

from app.factors import builtin
from app.factors.base import (
    Bar,
    BaseFactor,
    Bucket,
    FactorContext,
    FactorParamSpec,
    FactorRegistryError,
    FactorResult,
    MinutePoint,
    all_factors,
    get_factor,
    register_factor,
)
from app.factors.cache import FactorResultCache, factor_cache_key, get_or_compute
from app.factors.registry import (
    ResolvedParams,
    export_schemas,
    get_params,
    override_params,
    resolve_params,
    sync_definitions,
)
from app.factors.stats import BucketStats, FactorSample, SegmentStats, effectiveness

__all__ = [
    "Bar",
    "BaseFactor",
    "Bucket",
    "BucketStats",
    "FactorContext",
    "FactorParamSpec",
    "FactorRegistryError",
    "FactorResult",
    "FactorResultCache",
    "FactorSample",
    "MinutePoint",
    "ResolvedParams",
    "SegmentStats",
    "all_factors",
    "builtin",
    "effectiveness",
    "export_schemas",
    "factor_cache_key",
    "get_factor",
    "get_or_compute",
    "get_params",
    "override_params",
    "register_factor",
    "resolve_params",
    "sync_definitions",
]
