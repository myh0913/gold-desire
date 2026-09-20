"""开盘涨幅类因子（S2 硬门槛 + S4 加分项）。

**字段**（readme §10 口径，小数口径百分比）：
- ``t.open_pct``：D+1（次日）开盘涨幅。
- ``d_open_pct``：首阴开盘涨幅。
- ``t1.open_pct``：D+2（再下日）开盘涨幅。

**档位依据**：
- S2 硬门槛 ``t.open_pct <= -0.03``（低开确认恐慌，readme §4.1）；
- S2 加分项「首阴开盘 0~+3%」（readme §4.2）；
- S4 加分项 ①「次日开盘 0~+3%」（readme §5.2）；
- readme §9「次日开盘：≤-3% 最强，≥+3% 转负」。

各边界均为可配置参数。
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

__all__ = ["FirstYinOpenPct", "NextDayOpenPct", "NextNextDayOpenPct"]


def _open_pct(bar: Bar | None) -> float | None:
    """由日线计算开盘涨幅（相对昨收）；数据不足返回 ``None``。"""
    if bar is None or bar.pre_close == 0:
        return None
    return (bar.open - bar.pre_close) / bar.pre_close


def _numeric(value: Any) -> float | None:
    """把指标值转为 ``float``；非数值返回 ``None``。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class _OpenPctFactor(BaseFactor):
    """开盘涨幅类基类：读指标，缺失时回退到对应日线自行计算。"""

    metric_key: ClassVar[str] = ""

    def _bar(self, ctx: FactorContext) -> Bar | None:
        """返回用于回退计算的日线。"""
        return None

    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """读取 :attr:`metric_key`，缺失时用日线回退。"""
        value = _numeric(ctx.metric(self.metric_key))
        if value is not None:
            return self.result(value, params, detail={self.metric_key: value, "origin": "metric"})
        bar = self._bar(ctx)
        value = _open_pct(bar)
        if value is None:
            return self.missing(self.metric_key)
        return self.result(
            value,
            params,
            detail={
                self.metric_key: value,
                "origin": "computed",
                "open": bar.open if bar else None,
                "pre_close": bar.pre_close if bar else None,
            },
        )


@register_factor
class NextDayOpenPct(_OpenPctFactor):
    """D+1 开盘涨幅（readme §4.1 S2 硬门槛 / §5.2 S4 加分①）。"""

    factor_id = "next_day_open_pct"
    label = "次日开盘涨幅"
    category = "open_pct"
    description = "D+1 开盘涨幅；≤-3% 为 S2 硬门槛，0~+3% 为 S4 加分项①。"
    metric_key = "t.open_pct"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="panic_open",
            label="低开硬门槛",
            type="percent",
            default=-0.03,
            min=-1.0,
            max=0.0,
            step=0.01,
            unit="小数",
            description="S2 硬门槛：开盘涨幅 ≤ 该值；默认 -0.03（readme §4.1）。",
        ),
        FactorParamSpec(
            key="bonus_low",
            label="加分区间下界",
            type="percent",
            default=0.0,
            min=-1.0,
            max=1.0,
            step=0.01,
            unit="小数",
            description="S4 加分①区间下界；默认 0（readme §5.2）。",
        ),
        FactorParamSpec(
            key="bonus_high",
            label="加分区间上界",
            type="percent",
            default=0.03,
            min=-1.0,
            max=1.0,
            step=0.01,
            unit="小数",
            description="S4 加分①区间上界（不含）；默认 0.03（readme §5.2）。",
        ),
    )

    def _bar(self, ctx: FactorContext) -> Bar | None:
        return ctx.t_bar

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``<=-3%`` / ``-3~0%`` / ``0~+3%`` / ``>=+3%``。"""
        low = float(self.param(params, "panic_open"))
        mid = float(self.param(params, "bonus_low"))
        high = float(self.param(params, "bonus_high"))
        return [
            Bucket(f"<={format_signed_percent(low)}", hi=low, hi_inclusive=True),
            Bucket(
                f"{signed_percent_number(low)}~{format_signed_percent(mid)}",
                lo=low,
                lo_inclusive=False,
                hi=mid,
                hi_inclusive=False,
            ),
            Bucket(
                f"{signed_percent_number(mid)}~{format_signed_percent(high)}",
                lo=mid,
                lo_inclusive=True,
                hi=high,
                hi_inclusive=False,
            ),
            Bucket(f">={format_signed_percent(high)}", lo=high, lo_inclusive=True),
        ]


@register_factor
class FirstYinOpenPct(_OpenPctFactor):
    """首阴开盘涨幅（readme §4.2 S2 加分项 0~+3%）。"""

    factor_id = "first_yin_open_pct"
    label = "首阴开盘涨幅"
    category = "open_pct"
    description = "首阴开盘涨幅；0~+3% 为 S2 加分项（readme §4.2）。"
    metric_key = "d_open_pct"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="bonus_low",
            label="加分区间下界",
            type="percent",
            default=0.0,
            min=-1.0,
            max=1.0,
            step=0.01,
            unit="小数",
            description="加分区间下界；默认 0（readme §4.2）。",
        ),
        FactorParamSpec(
            key="bonus_high",
            label="加分区间上界",
            type="percent",
            default=0.03,
            min=-1.0,
            max=1.0,
            step=0.01,
            unit="小数",
            description="加分区间上界（不含）；默认 0.03（readme §4.2）。",
        ),
    )

    def _bar(self, ctx: FactorContext) -> Bar | None:
        return ctx.d_bar

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``<0%`` / ``0~+3%`` / ``>=+3%``。"""
        mid = float(self.param(params, "bonus_low"))
        high = float(self.param(params, "bonus_high"))
        return [
            Bucket(f"<{format_signed_percent(mid)}", hi=mid, hi_inclusive=False),
            Bucket(
                f"{signed_percent_number(mid)}~{format_signed_percent(high)}",
                lo=mid,
                lo_inclusive=True,
                hi=high,
                hi_inclusive=False,
            ),
            Bucket(f">={format_signed_percent(high)}", lo=high, lo_inclusive=True),
        ]


@register_factor
class NextNextDayOpenPct(_OpenPctFactor):
    """D+2 开盘涨幅（readme §10 ``t1.open_pct``）。"""

    factor_id = "next_next_day_open_pct"
    label = "再下日开盘涨幅"
    category = "open_pct"
    description = "D+2 开盘涨幅（readme §10 t1.open_pct）。"
    metric_key = "t1.open_pct"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="panic_open",
            label="低开阈值",
            type="percent",
            default=-0.03,
            min=-1.0,
            max=0.0,
            step=0.01,
            unit="小数",
            description="低开阈值；默认 -0.03。",
        ),
        FactorParamSpec(
            key="bonus_low",
            label="区间下界",
            type="percent",
            default=0.0,
            min=-1.0,
            max=1.0,
            step=0.01,
            unit="小数",
            description="中间区间下界；默认 0。",
        ),
        FactorParamSpec(
            key="bonus_high",
            label="区间上界",
            type="percent",
            default=0.03,
            min=-1.0,
            max=1.0,
            step=0.01,
            unit="小数",
            description="中间区间上界（不含）；默认 0.03。",
        ),
    )

    def _bar(self, ctx: FactorContext) -> Bar | None:
        return ctx.t1_bar

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``<=-3%`` / ``-3~0%`` / ``0~+3%`` / ``>=+3%``。"""
        low = float(self.param(params, "panic_open"))
        mid = float(self.param(params, "bonus_low"))
        high = float(self.param(params, "bonus_high"))
        return [
            Bucket(f"<={format_signed_percent(low)}", hi=low, hi_inclusive=True),
            Bucket(
                f"{signed_percent_number(low)}~{format_signed_percent(mid)}",
                lo=low,
                lo_inclusive=False,
                hi=mid,
                hi_inclusive=False,
            ),
            Bucket(
                f"{signed_percent_number(mid)}~{format_signed_percent(high)}",
                lo=mid,
                lo_inclusive=True,
                hi=high,
                hi_inclusive=False,
            ),
            Bucket(f">={format_signed_percent(high)}", lo=high, lo_inclusive=True),
        ]
