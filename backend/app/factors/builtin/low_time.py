"""首阴低点时段因子。

**含义**：首阴全天最低点所处的分时索引（分钟序号）。
**字段**：``mp_D.low_time_i``（readme §10「首阴全天最低点分时索引」，``mp_D`` = D 日分时派生指标）。
**单位**：分钟序号（0~239）。
**档位依据**：readme §9「首阴低点时段：≥90 分、30~90 分均可用」；
readme §5.2 S4 加分项 ④「≥90 分」与 ⑥「30~90 分」（二者互斥，见 §14.6）。

边界 ``mid_time`` / ``bonus_time`` 可配置，默认取自 readme。
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
    MinutePoint,
    format_count,
    register_factor,
)

__all__ = ["FirstYinLowTime"]


def _low_time_from_minutes(points: tuple[MinutePoint, ...]) -> int | None:
    """由 D 日分时序列取最低价所在分钟序号；无数据返回 ``None``。"""
    if not points:
        return None
    return min(points, key=lambda point: point.price).index


@register_factor
class FirstYinLowTime(BaseFactor):
    """首阴低点时段（readme §9 / §5.2）。"""

    factor_id = "first_yin_low_time"
    label = "首阴低点时段"
    category = "low_time"
    description = "首阴全天最低点分时索引；≥90 与 30~90 为 S4 加分项。"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="mid_time",
            label="早盘分界",
            type="int",
            default=30,
            min=0,
            max=239,
            step=1,
            unit="分钟",
            description="早盘分界（含）；默认 30（readme §9）。",
        ),
        FactorParamSpec(
            key="bonus_time",
            label="尾盘分界",
            type="int",
            default=90,
            min=0,
            max=239,
            step=1,
            unit="分钟",
            description="尾盘分界（含）；默认 90，S4 加分项④阈值（readme §5.2）。",
        ),
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``<30`` / ``30~90`` / ``>=90``。"""
        mid = int(self.param(params, "mid_time"))
        bonus = int(self.param(params, "bonus_time"))
        return [
            Bucket(f"<{format_count(mid)}", hi=mid, hi_inclusive=False),
            Bucket(
                f"{format_count(mid)}~{format_count(bonus)}",
                lo=mid,
                lo_inclusive=True,
                hi=bonus,
                hi_inclusive=False,
            ),
            Bucket(f">={format_count(bonus)}", lo=bonus, lo_inclusive=True),
        ]

    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """读取 ``mp_D.low_time_i``，缺失时由分时序列回退计算。"""
        raw = ctx.metric("mp_D.low_time_i")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            fallback = _low_time_from_minutes(ctx.minute_d)
            if fallback is None:
                return self.missing("mp_D.low_time_i")
            return self.result(
                fallback,
                params,
                detail={"mp_D.low_time_i": fallback, "origin": "computed"},
            )
        value = int(raw)
        return self.result(value, params, detail={"mp_D.low_time_i": value, "origin": "metric"})
