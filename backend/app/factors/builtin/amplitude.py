"""首阴振幅因子。

**含义**：首阴当日振幅 = ``(最高价 - 最低价) / 昨收``。
**字段**：``d_amp_pct``（小数口径，0.08 = 8%）。
**单位**：小数（0~1）。
**档位依据**：readme §9「首阴振幅 ≥8% 两路共有硬门槛；5~8% 转负」，
readme §4.1 / §5.1 两条硬门槛均为 ``d_amp_pct >= 0.08``。

阈值 ``low_amplitude`` / ``min_amplitude`` 均为可配置参数，计算与档位划分一律从参数读取。
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
    format_percent,
    percent_number,
    register_factor,
)

__all__ = ["FirstYinAmplitude"]


def _amplitude_from_bar(bar: Bar | None) -> float | None:
    """由日线计算振幅；数据不足返回 ``None``。"""
    if bar is None or bar.pre_close == 0:
        return None
    return (bar.high - bar.low) / bar.pre_close


@register_factor
class FirstYinAmplitude(BaseFactor):
    """首阴振幅（readme §9 / §4.1 / §5.1）。"""

    factor_id = "first_yin_amplitude"
    label = "首阴振幅"
    category = "amplitude"
    description = "首阴当日振幅 =(最高-最低)/昨收；小数口径 0.08=8%，两路共有硬门槛。"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="low_amplitude",
            label="低振幅上界",
            type="percent",
            default=0.05,
            min=0.0,
            max=1.0,
            step=0.01,
            unit="小数",
            description="小于等于该值归入「低振幅」档；默认 0.05（5%）。",
        ),
        FactorParamSpec(
            key="min_amplitude",
            label="硬门槛振幅",
            type="percent",
            default=0.08,
            min=0.0,
            max=1.0,
            step=0.01,
            unit="小数",
            description="两路共有硬门槛；默认 0.08（8%），对应 readme §4.1/§5.1。",
        ),
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``<=5%`` / ``5~8%`` / ``>=8%``（边界随参数变化）。"""
        low = float(self.param(params, "low_amplitude"))
        gate = float(self.param(params, "min_amplitude"))
        return [
            Bucket(f"<={format_percent(low)}", hi=low, hi_inclusive=True),
            Bucket(
                f"{percent_number(low)}~{format_percent(gate)}",
                lo=low,
                lo_inclusive=False,
                hi=gate,
                hi_inclusive=False,
            ),
            Bucket(f">={format_percent(gate)}", lo=gate, lo_inclusive=True),
        ]

    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """读取 ``d_amp_pct``，缺失时回退到 D 日日线自行计算。"""
        metric = ctx.metric("d_amp_pct")
        if metric is not None:
            value = float(metric)
            return self.result(value, params, detail={"d_amp_pct": value, "origin": "metric"})

        bar = ctx.d_bar
        computed = _amplitude_from_bar(bar)
        if computed is None or bar is None:
            return self.missing("d_amp_pct")
        return self.result(
            computed,
            params,
            detail={
                "d_amp_pct": computed,
                "origin": "computed",
                "high": bar.high,
                "low": bar.low,
                "pre_close": bar.pre_close,
            },
        )
