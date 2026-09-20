"""次日收盘涨幅因子。

**含义**：D+1 收盘涨幅（相对 D 日收盘）。
**字段**：``t.close_pct``（``t`` = D+1 全天字段对象，readme §10）。
**单位**：小数口径百分比（0.02 = +2%）。
**档位依据**：readme §9「次日收盘涨幅：≥0% 较优」；readme §5.2 S4 加分项 ⑤「次日收盘 ≥ 0%」。

边界 ``stop_low`` / ``bonus_low`` 可配置，默认取自 readme。
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
    format_signed_percent,
    register_factor,
    signed_percent_number,
)

__all__ = ["NextDayClosePct"]


def _close_pct(bar: Bar | None) -> float | None:
    """由日线计算收盘涨幅（相对昨收）；数据不足返回 ``None``。"""
    if bar is None or bar.pre_close == 0:
        return None
    return (bar.close - bar.pre_close) / bar.pre_close


@register_factor
class NextDayClosePct(BaseFactor):
    """次日收盘涨幅（readme §9 / §5.2）。"""

    factor_id = "next_day_close_pct"
    label = "次日收盘涨幅"
    category = "close_pct"
    description = "D+1 收盘涨幅；≥0% 为 S4 加分项⑤（readme §5.2）。"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="stop_low",
            label="深跌上界",
            type="percent",
            default=-0.05,
            min=-1.0,
            max=0.0,
            step=0.01,
            unit="小数",
            description="深跌档上界（含）；默认 -0.05。",
        ),
        FactorParamSpec(
            key="bonus_low",
            label="加分下界",
            type="percent",
            default=0.0,
            min=-1.0,
            max=1.0,
            step=0.01,
            unit="小数",
            description="S4 加分项⑤下界（含）；默认 0（readme §5.2）。",
        ),
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``<=-5%`` / ``-5~0%`` / ``>=0%``。"""
        low = float(self.param(params, "stop_low"))
        bonus = float(self.param(params, "bonus_low"))
        return [
            Bucket(f"<={format_signed_percent(low)}", hi=low, hi_inclusive=True),
            Bucket(
                f"{signed_percent_number(low)}~{format_signed_percent(bonus)}",
                lo=low,
                lo_inclusive=False,
                hi=bonus,
                hi_inclusive=True,
            ),
            Bucket(f">={format_signed_percent(bonus)}", lo=bonus, lo_inclusive=True),
        ]

    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """读取 ``t.close_pct``，缺失时由 D+1 日线回退计算。"""
        raw = ctx.metric("t.close_pct")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            fallback = _close_pct(ctx.t_bar)
            if fallback is None:
                return self.missing("t.close_pct")
            return self.result(
                fallback, params, detail={"t.close_pct": fallback, "origin": "computed"}
            )
        value = float(raw)
        return self.result(value, params, detail={"t.close_pct": value, "origin": "metric"})
