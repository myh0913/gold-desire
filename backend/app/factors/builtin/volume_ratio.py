"""量比类因子族（S4 核心硬门槛 + S2 加分项）。

**字段**（readme §10 口径，比值无量纲）：
- ``t_vol_vs_d``：D+1 全天量 / 首阴全天量。
- ``vol_vs_wave_peak``：首阴量 / 连板峰值量。
- ``vol_vs_prev``：首阴量 / 前日量。
- ``vol_vs_wave_mean``：首阴量 / 连板均量。

**档位依据**：
- S4 硬门槛 ``t_vol_vs_d < 0.6``（readme §5.1）；
- 单调性 ``<0.6 > 0.6~1.0 > 1.0~1.5``（readme §9）；
- 加分项 ``vol_vs_prev [1.0,1.5)``（readme §4.2）。

各档位边界均为可配置参数，默认取自上述 readme 数值。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.factors.base import (
    BaseFactor,
    Bucket,
    FactorContext,
    FactorParamSpec,
    FactorResult,
    format_ratio,
    register_factor,
)

__all__ = ["NextDayVolVsFirstYin", "VolVsPrev", "VolVsWaveMean", "VolVsWavePeak"]


def _ratio_metric(ctx: FactorContext, key: str) -> float | None:
    """读比值指标；非数值返回 ``None``。"""
    value = ctx.metric(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _four_band(s1: float, s2: float, s3: float) -> list[Bucket]:
    """``<s1`` / ``[s1,s2)`` / ``[s2,s3)`` / ``>=s3`` 四档（半开区间，互不重叠）。"""
    return [
        Bucket(f"<{format_ratio(s1)}", hi=s1, hi_inclusive=False),
        Bucket(
            f"{format_ratio(s1)}~{format_ratio(s2)}",
            lo=s1,
            lo_inclusive=True,
            hi=s2,
            hi_inclusive=False,
        ),
        Bucket(
            f"{format_ratio(s2)}~{format_ratio(s3)}",
            lo=s2,
            lo_inclusive=True,
            hi=s3,
            hi_inclusive=False,
        ),
        Bucket(f">={format_ratio(s3)}", lo=s3, lo_inclusive=True),
    ]


def _ratio_params(s1: float, s2: float, s3: float, note: str) -> tuple[FactorParamSpec, ...]:
    """构造量比类三边界参数（键名固定为 s1/s2/s3）。"""
    return (
        FactorParamSpec(
            key="s1",
            label="第一档上界",
            type="float",
            default=s1,
            min=0.0,
            step=0.1,
            unit="比值",
            description=note,
        ),
        FactorParamSpec(
            key="s2",
            label="第二档上界",
            type="float",
            default=s2,
            min=0.0,
            step=0.1,
            unit="比值",
            description="第二档上界（含）。",
        ),
        FactorParamSpec(
            key="s3",
            label="第三档上界",
            type="float",
            default=s3,
            min=0.0,
            step=0.1,
            unit="比值",
            description="第三档上界（含）。",
        ),
    )


class _RatioFactor(BaseFactor):
    """量比类因子基类：读取指标并分档，边界参数键名统一为 ``s1`` / ``s2`` / ``s3``。"""

    metric_key: ClassVar[str] = ""

    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """读取 :attr:`metric_key` 并判定档位。"""
        value = _ratio_metric(ctx, self.metric_key)
        if value is None:
            return self.missing(self.metric_key)
        return self.result(value, params, detail={self.metric_key: value})

    def _edges(self, params: Mapping[str, Any]) -> tuple[float, float, float]:
        """取三个档位边界。"""
        return (
            float(self.param(params, "s1")),
            float(self.param(params, "s2")),
            float(self.param(params, "s3")),
        )


@register_factor
class NextDayVolVsFirstYin(_RatioFactor):
    """次日量 / 首阴量（readme §5.1 S4 核心硬门槛）。"""

    factor_id = "next_day_vol_vs_first_yin"
    label = "次日量/首阴量"
    category = "volume_ratio"
    description = "D+1 全天量 / 首阴全天量；<0.6 为 S4 硬门槛（readme §5.1）。"
    metric_key = "t_vol_vs_d"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="gate_ratio",
            label="缩量硬门槛",
            type="float",
            default=0.6,
            min=0.0,
            max=2.0,
            step=0.1,
            unit="比值",
            description="S4 硬门槛：小于该值视为缩量；默认 0.6（readme §5.1）。",
        ),
        *_ratio_params(0.6, 1.0, 1.5, "第一档上界，默认 0.6（readme §9）。"),
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``<0.6`` / ``0.6~1.0`` / ``1.0~1.5`` / ``>=1.5``。"""
        s1, s2, s3 = self._edges(params)
        return _four_band(s1, s2, s3)


@register_factor
class VolVsWavePeak(_RatioFactor):
    """首阴量 / 连板峰值量（readme §9 降级为观察）。"""

    factor_id = "vol_vs_wave_peak"
    label = "首阴量/连板峰值"
    category = "volume_ratio"
    description = "首阴量 / 连板峰值量；readme §9 降级为观察。"
    metric_key = "vol_vs_wave_peak"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = _ratio_params(
        0.8, 1.0, 1.5, "第一档上界，默认 0.8。"
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        s1, s2, s3 = self._edges(params)
        return _four_band(s1, s2, s3)


@register_factor
class VolVsPrev(_RatioFactor):
    """首阴量 / 前日量（readme §4.2 S2 加分项 [1.0,1.5)）。"""

    factor_id = "vol_vs_prev"
    label = "首阴量/前日量"
    category = "volume_ratio"
    description = "首阴量 / 前日量；[1.0,1.5) 为 S2 加分项（readme §4.2）。"
    metric_key = "vol_vs_prev"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = _ratio_params(
        1.0, 1.5, 2.5, "第一档上界，默认 1.0（加分区间下界）。"
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        s1, s2, s3 = self._edges(params)
        return _four_band(s1, s2, s3)


@register_factor
class VolVsWaveMean(_RatioFactor):
    """首阴量 / 连板均量（readme §9 降级为观察）。"""

    factor_id = "vol_vs_wave_mean"
    label = "首阴量/连板均量"
    category = "volume_ratio"
    description = "首阴量 / 连板均量；readme §9 降级为观察。"
    metric_key = "vol_vs_wave_mean"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = _ratio_params(
        1.5, 2.5, 4.0, "第一档上界，默认 1.5。"
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        s1, s2, s3 = self._edges(params)
        return _four_band(s1, s2, s3)
