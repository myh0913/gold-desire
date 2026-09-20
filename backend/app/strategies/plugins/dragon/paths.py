"""龙回头两路定义（readme §3 / §4 / §5 / §7）。

| 路 | 类型 | 买点 | 卖出 | 基础仓 | 加码 | 单路上限 |
|---|---|---|---|---|---|---|
| **S2 高位跳水型** | 稳健 | D+1 开盘 | 无止盈 + 3% 止损 / 持 1 可卖日 | 20% | ❌ | 20% |
| **S4 缩量反转型** | 弹性 | D+2 开盘 | 同上 | 20% | ✅ 按加分项 | 30% |

**买点/可卖日**（readme §1.2 T+1）：S2 于 ``T``（D+1）开盘买入 → 可卖日 ``T1``（D+2）；
S4 于 ``T1``（D+2）开盘买入 → 可卖日 ``T2``（D+3）。``sellable_day_fields`` 首位即第一个
可卖日，故**止损仅在可卖日生效**（买入当日无保护，readme §14.2）。

仓位与上限一律由**策略参数**提供（``base_position`` / ``s4_max_position_factor``），
本文件不写死数值。
"""

from __future__ import annotations

from app.engine.portfolio import PathSpec
from app.strategies.plugins.dragon.bonus import s2_bonus, s4_bonus
from app.strategies.plugins.dragon.gates import s2_gates, s4_gates

__all__ = [
    "PATH_S2",
    "PATH_S4",
    "build_paths",
    "s2_path",
    "s4_path",
]

#: 路标识。
PATH_S2 = "S2"
PATH_S4 = "S4"


def s2_path(*, base_position: float) -> PathSpec:
    """S2（D+1 开盘 · 高位跳水型）：固定基础仓、**不加码**（readme §4.2 / §14.8）。"""
    return PathSpec(
        path_id=PATH_S2,
        label="高位跳水型",
        priority=1,
        buy_day_field="T",
        buy_metrics_field="T",
        sellable_day_fields=("T1", "T2"),
        base_position=base_position,
        max_position_factor=1.0,
        scale_by_bonus=False,
        gates=s2_gates(),
        bonus=s2_bonus(),
    )


def s4_path(
    *,
    base_position: float,
    max_position_factor: float,
    scale_by_bonus: bool = True,
) -> PathSpec:
    """S4（D+2 开盘 · 缩量反转型）：按加分项加码至单路上限（readme §5.2 / §7.1）。"""
    return PathSpec(
        path_id=PATH_S4,
        label="缩量反转型",
        priority=2,
        buy_day_field="T1",
        buy_metrics_field="T1",
        sellable_day_fields=("T2",),
        base_position=base_position,
        max_position_factor=max_position_factor,
        scale_by_bonus=scale_by_bonus,
        gates=s4_gates(),
        bonus=s4_bonus(),
    )


def build_paths(
    *,
    base_position: float,
    s4_max_position_factor: float,
    scale_by_bonus: bool = True,
) -> list[PathSpec]:
    """构造两路路径（``scale_by_bonus=False`` 即 readme §7.4「基础版」对照）。"""
    return [
        s2_path(base_position=base_position),
        s4_path(
            base_position=base_position,
            max_position_factor=s4_max_position_factor,
            scale_by_bonus=scale_by_bonus,
        ),
    ]
