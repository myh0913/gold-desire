"""龙回头加分项声明（readme §4.2 / §5.2 / §14.4 / §14.6）。

- **S2 加分项（6 条，展示标记，不可加码）**：readme §4.2 明确 S2 分层**非单调**
  （0 分 +1.23% > 1 分 +0.66%），强行加码会引入负贡献，故 :data:`S2_PATH` 的
  ``scale_by_bonus=False``，加分项只用于提示卡片展示。
- **S4 加分项（6 条，可加码）**：全量口径单调（readme §5.3），按分加码
  （readme §7.1 / §14.5）。

阈值来源：绝大多数加分项阈值取自**因子参数**（如 ``next_day_open_pct.bonus_low`` /
``vol_vs_prev.s1`` / ``continue_boards.bonus_boards`` / ``first_yin_low_time.bonus_time`` …）。

**唯一例外**：S2 加分项「首阴最低 >-2%（浅）」（readme §4.2）对应字段 ``d_low_pct``，
但当前因子集中**没有**对应因子（readme §9 列了该因子结论，因子注册表未注册
``first_yin_low_pct``；本任务不得修改 ``app/factors/``）。故该阈值改由**策略参数**
``s2_bonus_low_floor`` 提供（经 ``factor_params["dragon"]`` 命名空间注入，仍是可配置参数，
非硬编码常量）。该加分项**仅展示**，不影响任何仓位或收益数值。

**互斥说明**（readme §14.6）：S4 加分项 ④``mp_D.low_time_i >= 90`` 与
⑥``30 <= mp_D.low_time_i < 90`` 互斥，同一样本不可能同时满足；故 6 条加分项实际最多满足 5 条。
"""

from __future__ import annotations

from typing import Any

from app.engine.portfolio import BonusSpec
from app.strategies.plugins.dragon.gates import (
    SHAPE_LATE_DIVE,
    SHAPE_RUSH_FADE,
    number,
    param_value,
)

__all__ = [
    "S2_BONUS_COUNT",
    "S4_BONUS_COUNT",
    "s2_bonus",
    "s4_bonus",
]


def _in_band(value: Any, low: Any, high: Any) -> bool:
    """判定 ``low <= value < high``（缺失视为不满足）。"""
    numeric = number(value)
    return numeric is not None and float(low) <= numeric < float(high)


def _ge(value: Any, threshold: Any) -> bool:
    """判定 ``value >= threshold``（缺失视为不满足）。"""
    numeric = number(value)
    return numeric is not None and numeric >= float(threshold)


def _shape_is(ctx: Any, expected: str) -> bool:
    """判定首阴形态是否等于给定枚举值。"""
    return ctx.metric("shape_label") == expected


def s2_bonus() -> tuple[BonusSpec, ...]:
    """S2 六条加分项（readme §4.2；**仅展示，不加码**）。"""
    return (
        BonusSpec(
            factor_id="first_yin_open_pct",
            label="首阴开盘 0~+3%",
            test=lambda ctx, params: _in_band(
                ctx.metric("d_open_pct"),
                param_value(params, "bonus_low"),
                param_value(params, "bonus_high"),
            ),
        ),
        BonusSpec(
            factor_id="vol_vs_prev",
            label="首阴量/前日量 [1.0,1.5)",
            test=lambda ctx, params: _in_band(
                ctx.metric("vol_vs_prev"),
                param_value(params, "s1"),
                param_value(params, "s2"),
            ),
        ),
        BonusSpec(
            factor_id="continue_boards",
            label="连板≥3",
            test=lambda ctx, params: _ge(ctx.metric("boards"), param_value(params, "bonus_boards")),
        ),
        BonusSpec(
            factor_id="continue_boards",
            label="连板≥4",
            test=lambda ctx, params: _ge(
                ctx.metric("boards"), param_value(params, "strong_boards")
            ),
        ),
        BonusSpec(
            factor_id="dragon",
            label="首阴最低 >-2%（浅）",
            test=lambda ctx, params: _ge(
                ctx.metric("d_low_pct"), param_value(params, "s2_bonus_low_floor")
            ),
        ),
        BonusSpec(
            factor_id="wave_one_word_count",
            label="一字板=0",
            test=lambda ctx, params: (
                number(ctx.metric("wave_one_word_cnt")) is not None
                and int(ctx.metric("wave_one_word_cnt")) < int(param_value(params, "min_count"))
            ),
        ),
    )


def s4_bonus() -> tuple[BonusSpec, ...]:
    """S4 六条加分项（readme §5.2；**可加码**）。"""
    return (
        BonusSpec(
            factor_id="next_day_open_pct",
            label="次日开盘 0~+3%",
            test=lambda ctx, params: _in_band(
                ctx.metric("t.open_pct"),
                param_value(params, "bonus_low"),
                param_value(params, "bonus_high"),
            ),
        ),
        BonusSpec(
            factor_id="first_yin_shape",
            label=f"首阴形态={SHAPE_LATE_DIVE}",
            test=lambda ctx, params: _shape_is(ctx, SHAPE_LATE_DIVE),
        ),
        BonusSpec(
            factor_id="first_yin_shape",
            label=f"首阴形态={SHAPE_RUSH_FADE}",
            test=lambda ctx, params: _shape_is(ctx, SHAPE_RUSH_FADE),
        ),
        BonusSpec(
            factor_id="first_yin_low_time",
            label="首阴低点时段≥90分",
            test=lambda ctx, params: _ge(
                ctx.metric("mp_D.low_time_i"), param_value(params, "bonus_time")
            ),
        ),
        BonusSpec(
            factor_id="next_day_close_pct",
            label="次日收盘≥0%",
            test=lambda ctx, params: _ge(
                ctx.metric("t.close_pct"), param_value(params, "bonus_low")
            ),
        ),
        BonusSpec(
            factor_id="first_yin_low_time",
            label="首阴低点时段 30~90分",
            test=lambda ctx, params: _in_band(
                ctx.metric("mp_D.low_time_i"),
                param_value(params, "mid_time"),
                param_value(params, "bonus_time"),
            ),
        ),
    )


#: 两路加分项条数（readme §14.4：各 6 条）。
S2_BONUS_COUNT = len(s2_bonus())
S4_BONUS_COUNT = len(s4_bonus())
