"""首阴收盘位置因子。

**含义**：首阴收盘价在当日振幅区间中的相对位置，``(收盘 - 最低) / (最高 - 最低)``，取值 0~1。
**字段**：``mp_D.close_pos``（``mp_D`` = D 日分时派生指标）。
**单位**：比例（0~1）。
**档位依据**：readme §9「首阴收盘位置：三段不一致（A 段负、C 段正）→ 剔除」，
即该因子**不纳入选股**，保留档位仅作观察与统计对照。

边界 ``low_pos`` / ``high_pos`` 可配置。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.factors.base import (
    Bar,
    BaseFactor,
    Bucket,
    FactorContext,
    FactorParamSpec,
    FactorResult,
    format_ratio,
    register_factor,
)

__all__ = ["FirstYinClosePos"]


def _close_pos_from_bar(bar: Bar | None) -> float | None:
    """由日线计算收盘位置；振幅为 0 或数据缺失返回 ``None``。"""
    if bar is None:
        return None
    span = bar.high - bar.low
    if span <= 0:
        return None
    return (bar.close - bar.low) / span


@register_factor
class FirstYinClosePos(BaseFactor):
    """首阴收盘位置（readme §9，已剔除，仅观察）。"""

    factor_id = "first_yin_close_pos"
    label = "首阴收盘位置"
    category = "close_pos"
    description = "首阴收盘在当日振幅中的位置；readme §9 已剔除，仅作观察统计。"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="low_pos",
            label="低位上界",
            type="float",
            default=0.3,
            min=0.0,
            max=1.0,
            step=0.05,
            unit="比例",
            description="低位档上界（含）；默认 0.3。",
        ),
        FactorParamSpec(
            key="high_pos",
            label="中位上界",
            type="float",
            default=0.6,
            min=0.0,
            max=1.0,
            step=0.05,
            unit="比例",
            description="中位档上界（含）；默认 0.6。",
        ),
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``<=0.3`` / ``0.3~0.6`` / ``>0.6``。"""
        low = float(self.param(params, "low_pos"))
        high = float(self.param(params, "high_pos"))
        return [
            Bucket(f"<={format_ratio(low)}", hi=low, hi_inclusive=True),
            Bucket(
                f"{format_ratio(low)}~{format_ratio(high)}",
                lo=low,
                lo_inclusive=False,
                hi=high,
                hi_inclusive=True,
            ),
            Bucket(f">{format_ratio(high)}", lo=high, lo_inclusive=False),
        ]

    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """读取 ``mp_D.close_pos``，缺失时由 D 日日线回退计算。"""
        raw = ctx.metric("mp_D.close_pos")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            fallback = _close_pos_from_bar(ctx.d_bar)
            if fallback is None:
                return self.missing("mp_D.close_pos")
            return self.result(
                fallback, params, detail={"mp_D.close_pos": fallback, "origin": "computed"}
            )
        value = float(raw)
        return self.result(value, params, detail={"mp_D.close_pos": value, "origin": "metric"})
