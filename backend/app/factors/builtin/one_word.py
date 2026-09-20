"""连板波一字板数量因子。

**含义**：连板波中出现的一字板个数。
**字段**：``wave_one_word_cnt``（readme §10「连板波一字板数量」；亦兼容别名
``wave_one_word_count``）。
**单位**：个。
**档位依据**：readme §9「一字板数量：0 个略优，差异小」；readme §4.2 S2 加分项「一字板=0」。

边界 ``min_count`` 可配置（默认 1，即 0 个为加分档）。
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

__all__ = ["WaveOneWordCount"]


@register_factor
class WaveOneWordCount(BaseFactor):
    """连板波一字板数量（readme §9 / §4.2）。"""

    factor_id = "wave_one_word_count"
    label = "连板波一字板数量"
    category = "one_word"
    description = "连板波一字板个数；=0 为 S2 加分项（readme §4.2）。"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="min_count",
            label="非零门槛",
            type="int",
            default=1,
            min=1,
            max=20,
            step=1,
            unit="个",
            description="≥1 归入「有」档；默认 1（0 个为加分档，readme §4.2）。",
        ),
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """``==0`` / ``>=1``。"""
        threshold = int(self.param(params, "min_count"))
        return [
            Bucket(
                f"=={format_count(threshold - 1)}",
                lo=threshold - 1,
                lo_inclusive=True,
                hi=threshold,
                hi_inclusive=False,
            ),
            Bucket(f">={format_count(threshold)}", lo=threshold, lo_inclusive=True),
        ]

    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """读取 ``wave_one_word_cnt``（兼容 ``wave_one_word_count``）。"""
        raw = ctx.metric("wave_one_word_cnt", "wave_one_word_count")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return self.missing("wave_one_word_cnt")
        value = int(raw)
        return self.result(value, params, detail={"wave_one_word_cnt": value})
