"""连板期量能趋势因子。

**含义**：末板量 / 首板量（连板期间量能是放大还是衰减）。
**字段**：``wave_vol_trend``（readme §10「末板量 / 首板量」）。
**单位**：比值（无量纲）。
**档位依据**：readme §9「连板期量能趋势：<0.7 递减是负项；[0.7,1.05] 平稳较优」。
旧版 S3 路曾以 ``[0.7,1.05]`` 为触发条件，一年数据下该路已并入 S2（readme §3），
故此处仅作加分/观察用档位。

边界 ``trend_low`` / ``trend_high`` 可配置，默认取自 readme §9。
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

__all__ = ["WaveVolTrend"]


@register_factor
class WaveVolTrend(BaseFactor):
    """连板期量能趋势（readme §9）。"""

    factor_id = "wave_vol_trend"
    label = "连板期量能趋势"
    category = "wave_trend"
    description = "末板量 / 首板量；<0.7 递减为负项，0.7~1.05 平稳较优（readme §9）。"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="trend_low",
            label="衰减上界",
            type="float",
            default=0.7,
            min=0.0,
            step=0.05,
            unit="比值",
            description="小于该值视为量能递减；默认 0.7（readme §9）。",
        ),
        FactorParamSpec(
            key="trend_high",
            label="平稳上界",
            type="float",
            default=1.05,
            min=0.0,
            step=0.05,
            unit="比值",
            description="平稳区间上界（不含）；默认 1.05（readme §9）。",
        ),
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``<0.7`` / ``0.7~1.05`` / ``>=1.05``。"""
        low = float(self.param(params, "trend_low"))
        high = float(self.param(params, "trend_high"))
        return [
            Bucket(f"<{format_ratio(low)}", hi=low, hi_inclusive=False),
            Bucket(
                f"{format_ratio(low)}~{format_ratio(high)}",
                lo=low,
                lo_inclusive=True,
                hi=high,
                hi_inclusive=False,
            ),
            Bucket(f">={format_ratio(high)}", lo=high, lo_inclusive=True),
        ]

    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """读取 ``wave_vol_trend``。"""
        raw = ctx.metric("wave_vol_trend")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return self.missing("wave_vol_trend")
        return self.result(float(raw), params, detail={"wave_vol_trend": float(raw)})
