"""首阴分时形态因子。

**含义**：首阴当日分时走势的分类标签。
**字段**：``shape_label``（字符串枚举：尾盘跳水 / 冲高回落 / 早盘急杀后横盘 /
低位横盘震荡 / 单边下跌）。
**单位**：无（枚举）。
**档位依据**：readme §9「分时形态：尾盘跳水 / 冲高回落 / 早盘急杀后横盘均可，
单边下跌较弱」；S2 硬门槛要求 ``shape_label == "尾盘跳水"``（readme §4.1），
S4 加分项 ②③ 分别要求尾盘跳水 / 冲高回落（readme §5.2）。

档位为 readme §9 的五类枚举；硬门槛形态通过参数 ``gate_shape`` 配置。
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
    register_factor,
)

__all__ = ["DEFAULT_SHAPES", "FirstYinShape"]

#: readme §9 / §10 定义的五类首阴分时形态。
DEFAULT_SHAPES = (
    "单边下跌",
    "尾盘跳水",
    "早盘急杀后横盘",
    "低位横盘震荡",
    "冲高回落",
)


@register_factor
class FirstYinShape(BaseFactor):
    """首阴分时形态（readme §9 / §4.1 / §5.2）。"""

    factor_id = "first_yin_shape"
    label = "首阴分时形态"
    category = "shape"
    description = "首阴当日分时形态标签；S2 硬门槛与 S4 加分项使用。"
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = (
        FactorParamSpec(
            key="gate_shape",
            label="硬门槛形态",
            type="enum",
            default="尾盘跳水",
            description="S2 路要求的形态；默认「尾盘跳水」，对应 readme §4.1。",
        ),
    )

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """每个形态取值为一个枚举档位。"""
        return [Bucket(label, equals=label) for label in DEFAULT_SHAPES]

    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """读取 ``shape_label``。"""
        shape = ctx.metric("shape_label")
        if not isinstance(shape, str):
            return self.missing("shape_label")
        return self.result(shape, params, detail={"shape_label": shape})
