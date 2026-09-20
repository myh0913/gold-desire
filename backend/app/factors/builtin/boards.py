"""连板数因子。

**含义**：连板波天数（至少 2 连板为前置条件）。
**字段**：``boards``（readme §10「连板波天数」）。
**单位**：个（板数）。
**档位依据**：readme §2.1「至少 2 连板」为前置门槛；readme §9「≥3 略优于 2 板」；
readme §4.2 S2 加分项「连板≥3」「连板≥4」。

档位为阈值阶梯：命中 ``>=4`` 者不再计入 ``>=3``；边界参数可配置。
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
    format_count,
    register_factor,
)

__all__ = ["ContinueBoards"]


@register_factor
class ContinueBoards(BaseFactor):
    """连板数（readme §2.1 / §9 / §4.2）。"""

    factor_id = "continue_boards"
    label = "连板数"
    category = "boards"
    description = "连板波天数；≥2 为前置条件，≥3/≥4 为 S2 加分项。"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="min_boards",
            label="最低连板",
            type="int",
            default=2,
            min=1,
            max=20,
            step=1,
            unit="个",
            description="形态前置条件：至少该连板数；默认 2（readme §2.1）。",
        ),
        FactorParamSpec(
            key="bonus_boards",
            label="加分连板",
            type="int",
            default=3,
            min=1,
            max=20,
            step=1,
            unit="个",
            description="S2 加分项：连板 ≥ 该值；默认 3（readme §4.2）。",
        ),
        FactorParamSpec(
            key="strong_boards",
            label="强连板",
            type="int",
            default=4,
            min=1,
            max=20,
            step=1,
            unit="个",
            description="S2 加分项：连板 ≥ 该值；默认 4（readme §4.2）。",
        ),
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``2`` / ``>=3`` / ``>=4``（阈值阶梯，互斥）。"""
        min_boards = int(self.param(params, "min_boards"))
        bonus = int(self.param(params, "bonus_boards"))
        strong = int(self.param(params, "strong_boards"))
        return [
            Bucket(
                format_count(min_boards),
                lo=min_boards,
                lo_inclusive=True,
                hi=bonus,
                hi_inclusive=False,
            ),
            Bucket(
                f">={format_count(bonus)}",
                lo=bonus,
                lo_inclusive=True,
                hi=strong,
                hi_inclusive=False,
            ),
            Bucket(f">={format_count(strong)}", lo=strong, lo_inclusive=True),
        ]

    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """读取 ``boards``。"""
        raw = ctx.metric("boards")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return self.missing("boards")
        return self.result(int(raw), params, detail={"boards": int(raw)})
