"""龙回头门控声明：情绪周期门控矩阵 + 两路硬门槛。

**门控矩阵**（:data:`GATE_MATRIX`）：由策略**自身声明**，框架只按协议读取
（spec「策略插件框架」：核心 SHALL NOT 维护以策略名为 key 的硬编码矩阵）。
readme 未给出龙回头的周期门控口径，故此处给出一份**文档化默认**：冰点/退潮禁用，
修复/加速放行，冰点转折/分歧半仓；后续可按需调整（改动只在本文件）。

**硬门槛**（:func:`s2_gates` / :func:`s4_gates`）：全部 AND 命中方可开仓，阈值一律从
**因子参数**读取（``first_yin_shape.gate_shape`` / ``first_yin_amplitude.min_amplitude`` /
``next_day_open_pct.panic_open`` / ``next_day_vol_vs_first_yin.gate_ratio``），
本文件不出现任何数值阈值常量（readme §4.1 / §5.1）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.engine.portfolio import GateSpec
from app.strategies.protocol import CycleState, GateRule

__all__ = [
    "GATE_MATRIX",
    "param_value",
    "s2_gates",
    "s4_gates",
]

#: 龙回头周期门控矩阵（策略自声明；核心零改动）。
GATE_MATRIX: Mapping[CycleState, GateRule] = {
    CycleState.ICE: GateRule(False, 0.0),
    CycleState.TURN: GateRule(True, 0.5),
    CycleState.REPAIR: GateRule(True, 1.0),
    CycleState.ACCEL: GateRule(True, 1.0),
    CycleState.DIVERGE: GateRule(True, 0.5),
    CycleState.RETREAT: GateRule(False, 0.0),
}

#: 形态枚举取值（readme §9 五类形态；属领域枚举，非数值阈值）。
SHAPE_LATE_DIVE = "尾盘跳水"
SHAPE_RUSH_FADE = "冲高回落"


def param_value(params: Mapping[str, Any], key: str) -> Any:
    """取阈值参数；缺失时**显式报错**（防止阈值被静默丢弃/硬编码回退）。

    Raises:
        KeyError: 参数缺失——阈值必须来自因子（或策略）参数。
    """
    value = params.get(key)
    if value is None:
        raise KeyError(f"缺少阈值参数 {key!r}；阈值必须来自因子/策略参数，不得硬编码")
    return value


def number(value: Any) -> float | None:
    """把指标值转为 ``float``；非数值返回 ``None``。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _at_least(ctx: Any, metric_key: str, params: Mapping[str, Any], key: str) -> bool:
    """判定 ``metric(metric_key) >= params[key]``（缺失视为不命中）。"""
    value = number(ctx.metric(metric_key))
    return value is not None and value >= float(param_value(params, key))


def _at_most(ctx: Any, metric_key: str, params: Mapping[str, Any], key: str) -> bool:
    """判定 ``metric(metric_key) <= params[key]``（缺失视为不命中）。"""
    value = number(ctx.metric(metric_key))
    return value is not None and value <= float(param_value(params, key))


def _below(ctx: Any, metric_key: str, params: Mapping[str, Any], key: str) -> bool:
    """判定 ``metric(metric_key) < params[key]``（缺失视为不命中）。"""
    value = number(ctx.metric(metric_key))
    return value is not None and value < float(param_value(params, key))


def _describe_at_least(ctx: Any, metric_key: str, params: Mapping[str, Any], key: str) -> str:
    """生成「字段=值 >= 阈值」描述。"""
    return f"{metric_key}={ctx.metric(metric_key)} >= {key}={param_value(params, key)}"


def _describe_at_most(ctx: Any, metric_key: str, params: Mapping[str, Any], key: str) -> str:
    """生成「字段=值 <= 阈值」描述。"""
    return f"{metric_key}={ctx.metric(metric_key)} <= {key}={param_value(params, key)}"


def _describe_below(ctx: Any, metric_key: str, params: Mapping[str, Any], key: str) -> str:
    """生成「字段=值 < 阈值」描述。"""
    return f"{metric_key}={ctx.metric(metric_key)} < {key}={param_value(params, key)}"


def s2_gates() -> tuple[GateSpec, ...]:
    """S2（D+1 开盘 · 高位跳水型）三条硬门槛（readme §4.1，全部 AND）。"""
    return (
        GateSpec(
            factor_id="first_yin_shape",
            label="首阴分时形态=硬门槛形态",
            test=lambda ctx, params: ctx.metric("shape_label") == param_value(params, "gate_shape"),
            describe=lambda ctx, params: (
                f"shape_label={ctx.metric('shape_label')!r} == "
                f"gate_shape={param_value(params, 'gate_shape')!r}"
            ),
        ),
        GateSpec(
            factor_id="first_yin_amplitude",
            label="首阴振幅≥硬门槛",
            test=lambda ctx, params: _at_least(ctx, "d_amp_pct", params, "min_amplitude"),
            describe=lambda ctx, params: _describe_at_least(
                ctx, "d_amp_pct", params, "min_amplitude"
            ),
        ),
        GateSpec(
            factor_id="next_day_open_pct",
            label="次日开盘≤低开硬门槛",
            test=lambda ctx, params: _at_most(ctx, "t.open_pct", params, "panic_open"),
            describe=lambda ctx, params: _describe_at_most(ctx, "t.open_pct", params, "panic_open"),
        ),
    )


def s4_gates() -> tuple[GateSpec, ...]:
    """S4（D+2 开盘 · 缩量反转型）两条硬门槛（readme §5.1，全部 AND）。"""
    return (
        GateSpec(
            factor_id="next_day_vol_vs_first_yin",
            label="次日量/首阴量<缩量门槛",
            test=lambda ctx, params: _below(ctx, "t_vol_vs_d", params, "gate_ratio"),
            describe=lambda ctx, params: _describe_below(ctx, "t_vol_vs_d", params, "gate_ratio"),
        ),
        GateSpec(
            factor_id="first_yin_amplitude",
            label="首阴振幅≥硬门槛",
            test=lambda ctx, params: _at_least(ctx, "d_amp_pct", params, "min_amplitude"),
            describe=lambda ctx, params: _describe_at_least(
                ctx, "d_amp_pct", params, "min_amplitude"
            ),
        ),
    )
