"""卖出规则引擎：无止盈 + 3% 止损 + 持 1 个可卖日（readme §6 / §14）。

口径逐条对齐 readme：

- **不设止盈**（:attr:`SellRuleConfig.take_profit` 默认 ``None``）。readme §6 明确旧版
  「8% 止盈」为负贡献，且其对比行同时改了止损档，不能单独归因于止盈；故默认无止盈。
- **止损 -3%**：以**可卖日分钟收盘价序列逐点判定**，当某点收益 ``<= stop_loss`` 时按
  **止损价成交**（回测无跳空/滑点假设，返回恰好 ``stop_loss``）。实盘遇跳空/滑点，
  真实成交价更差（readme §12 / §14.1）。
- **止损仅在可卖日生效**（readme §14.2）：S2 在 D+1 开盘买入、可卖日为 D+2，故
  **D+1 全天无止损保护**；S4 在 D+2 开盘买入、可卖日为 D+3，D+2 全天无保护。
  调用方只把**可卖日**的分时序列传进来，本模块不对买入当日做任何保护——这是本设计
  与「随时可砍」直觉不符的关键点，务必注意。
- **持 1 个可卖日**：持满则按可卖日**收盘价**了结；若当日一字跌停无法卖出，回测按收盘价
  成交（乐观假设，readme §14.3），实盘会延续到下一可卖日，需人工处理。
- 支持 ``hold_sellable_days=2`` 以复现 readme §6「持 1 日 vs 持 2 日」的敏感性对照，
  默认仍为 1。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "DEFAULT_HOLD_SELLABLE_DAYS",
    "DEFAULT_STOP_LOSS",
    "SellRuleConfig",
    "SellRuleResult",
    "evaluate",
]

#: 默认止损档（readme §6：-3%，分钟级触发）。
DEFAULT_STOP_LOSS = -0.03

#: 默认持有可卖日数（readme §6：持 1 个可卖日）。
DEFAULT_HOLD_SELLABLE_DAYS = 1

#: 退出原因：触发止损。
EXIT_STOP_LOSS = "stop_loss"
#: 退出原因：持满按收盘价了结。
EXIT_CLOSE = "close"


@dataclass(frozen=True, slots=True)
class SellRuleConfig:
    """卖出规则参数（两路统一，readme §6）。

    Attributes:
        take_profit: 止盈档（小数口径）；``None`` 表示不设止盈（默认）。
        stop_loss: 止损档（小数口径，负数）；``None`` 表示不设止损。
        hold_sellable_days: 持有几个可卖日；默认 1（持满按收盘价了结）。
    """

    take_profit: float | None = None
    stop_loss: float | None = DEFAULT_STOP_LOSS
    hold_sellable_days: int = DEFAULT_HOLD_SELLABLE_DAYS


@dataclass(frozen=True, slots=True)
class SellRuleResult:
    """单笔卖出的撮合结果。

    Attributes:
        return_pct: 实现收益（小数口径，0.0251 = +2.51%）。
        exit_reason: 退出原因，``"stop_loss"`` 或 ``"close"``。
        exit_index: 在**遍历序列**（持多日时为各可卖日按序拼接）中的退出点下标。
        max_gain: 遍历序列上的最大浮盈（小数口径，用于「均上冲」统计）。
        max_drawdown: 遍历序列上的最大浮亏（小数口径，用于「均回撤」统计）；
            为**路径最差偏移**，止损触发时可能比实现收益更深（如路径触及 -4% 但按 -3% 成交）。
    """

    return_pct: float
    exit_reason: str
    exit_index: int
    max_gain: float
    max_drawdown: float


def evaluate(
    buy_price: float | None,
    minute_series: Sequence[Sequence[float]],
    take_profit: float | None = None,
    stop_loss: float | None = DEFAULT_STOP_LOSS,
    hold_sellable_days: int = DEFAULT_HOLD_SELLABLE_DAYS,
) -> SellRuleResult | None:
    """按卖出规则撮合单笔交易，返回实现收益与退出明细。

    Args:
        buy_price: 买入价；``None`` / ``<= 0`` 表示不可买（返回 ``None``）。
        minute_series: **可卖日**的分钟收盘价序列，按时间顺序排列；第 0 项为第一个可卖日，
            第 1 项为第二个可卖日。买入当日**不在**其中（止损仅在可卖日生效）。
        take_profit: 止盈档；``None`` 表示不设（默认）。
        stop_loss: 止损档；``None`` 表示不设。
        hold_sellable_days: 持有几个可卖日；``<= 0`` 视为 1。

    Returns:
        :class:`SellRuleResult`；买入价或序列缺失时返回 ``None``。

    Note:
        止损触发时返回**恰好** ``stop_loss``（无跳空假设）；实盘按实际成交价，滑点使结果更差。
        持满未触发止损时按最后一个可卖日的**收盘价**了结（一字跌停无法卖出的乐观假设）。
    """
    if buy_price is None or buy_price <= 0:
        return None
    hold = max(1, hold_sellable_days)
    usable = [series for series in minute_series[:hold] if series]
    if not usable:
        return None

    traversed: list[float] = [price for series in usable for price in series]

    max_gain = 0.0
    max_drawdown = 0.0
    for index, price in enumerate(traversed):
        value = price / buy_price - 1
        if value > max_gain:
            max_gain = value
        if value < max_drawdown:
            max_drawdown = value
        if stop_loss is not None and value <= stop_loss:
            return SellRuleResult(
                return_pct=stop_loss,
                exit_reason=EXIT_STOP_LOSS,
                exit_index=index,
                max_gain=max_gain,
                max_drawdown=max_drawdown,
            )
        if take_profit is not None and value >= take_profit:
            return SellRuleResult(
                return_pct=take_profit,
                exit_reason="take_profit",
                exit_index=index,
                max_gain=max_gain,
                max_drawdown=max_drawdown,
            )

    return SellRuleResult(
        return_pct=traversed[-1] / buy_price - 1,
        exit_reason=EXIT_CLOSE,
        exit_index=len(traversed) - 1,
        max_gain=max_gain,
        max_drawdown=max_drawdown,
    )
