"""龙回头样本的**日线 / 分时判定助手**（纯函数，无 I/O）。

从 :mod:`app.engine.dragon_samples` 拆出（规范：单文件 ≤500 行）。核心职责：

1. :func:`_with_pre_close` 给日线序列回填昨收（库中 ``pre_close`` 实测全 NULL，
   涨停 / 振幅 / 首阴 / suspect 判定全部依赖昨收）；
2. 涨停 / 一字 / 三板组 / suspect（除权坏数据）等判定；
3. 由日线 / 分钟价派生 :class:`~app.engine.dragon_model.DayMetrics` /
   :class:`~app.engine.dragon_model.MinuteMetrics`。

阈值与 :mod:`app.engine.guards` 同源（``_SUSPECT_PCT = MAX_DAY_MOVE``），
由 :mod:`tests.test_guards` 防漂移。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.engine.dragon_model import DayMetrics, MinuteMetrics
from app.engine.guards import MAX_DAY_MOVE

#: 主板涨停判定阈值（相对昨收涨幅，含 10% 涨跌幅四舍五入误差）。
_LIMIT_UP_PCT = 0.095

#: suspect 阈值（对齐旧项目 analyzer.SUSPECT_PCT）：主板 ±10% 下越界只可能是
#: 除权除息 / 送转 / 坏数据 → 整票拒收（与 :mod:`app.engine.guards` 同源）。
_SUSPECT_PCT = MAX_DAY_MOVE

#: 三板组（sanbanzu）量能特征阈值（对齐旧项目 analyzer.is_sanbanzu）。
_SANBANZU_FIRST_VS_PRE = 1.2
_SANBANZU_ONE_WORD_VS_FIRST = 0.1


def _to_float(value: Any) -> float | None:
    """尽力转 ``float``；``None`` / 不可解析 → ``None``（不抛异常）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class _BarView:
    """日线**只读视图** + 回填的昨收。

    用副本而非直接改 ORM 实例，避免把 ``pre_close`` 写脏回库。
    """

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    pre_close: float | None
    volume_shares: float


def _with_pre_close(bars: Sequence[Any]) -> list[_BarView]:
    """给日线序列**回填昨收**，返回只读视图。

    为什么必须回填：上游日线不带昨收，库中 ``daily_bars.pre_close`` 实测
    **4593/4593 全为 NULL**，而涨停判定 / 振幅 / 首阴形态 / 样本构造**全部**依赖
    昨收。此前 ``_is_limit_up`` 直接 ``float(bar.pre_close)`` 抛 ``TypeError``，
    导致 dragon 策略每次运行必崩（实测当日 239 次 pool + 239 次 intraday 全落
    error 报告，且一条建议都没产出）。

    昨收取**序列中前一根日线的收盘价**：日线按日期升序、同一股票同一交易日唯一，
    停牌/缺失日自然跳过——此时昨收即最近一次成交的收盘价，语义不变。
    行上已有 ``pre_close`` 时优先用它；首根无昨收 → ``None``。
    """
    views: list[_BarView] = []
    prev_close: float | None = None
    for bar in bars:
        close = _to_float(getattr(bar, "close", None))
        views.append(
            _BarView(
                trade_date=bar.trade_date,
                open=_to_float(getattr(bar, "open", None)) or 0.0,
                high=_to_float(getattr(bar, "high", None)) or 0.0,
                low=_to_float(getattr(bar, "low", None)) or 0.0,
                close=close or 0.0,
                pre_close=_to_float(getattr(bar, "pre_close", None)) or prev_close,
                volume_shares=_to_float(getattr(bar, "volume_shares", None)) or 0.0,
            )
        )
        prev_close = close
    return views


def _is_limit_up(bar: Any) -> bool:
    """是否涨停（主板 10%，含四舍五入容差）。

    ``pre_close`` 缺失 → **判否**：缺少昨收无法计算涨停幅度，不得臆断。
    日线序列应先经 :func:`_with_pre_close` 回填昨收。
    """
    pre_close = _to_float(getattr(bar, "pre_close", None))
    close = _to_float(getattr(bar, "close", None))
    if pre_close is None or close is None or pre_close <= 0:
        return False
    return close / pre_close - 1 >= _LIMIT_UP_PCT


def _is_one_word(bar: Any) -> bool:
    """是否一字板（涨停且全天未离开涨停价）。"""
    if not _is_limit_up(bar):
        return False
    low = _to_float(getattr(bar, "low", None))
    close = _to_float(getattr(bar, "close", None))
    return low is not None and close is not None and low >= close - 1e-6


def _is_sanbanzu_wave(wave: Sequence[Any], pre_bar: Any | None) -> bool:
    """是否三板组（对齐旧项目 ``analyzer.is_sanbanzu``，readme §2.3 排除项）。

    判定（全部满足）：恰好 3 连板；后两板均一字；首板量 ≤ 波前一日量 ×1.2
    （波前一日缺失时放行该条——无法证伪，避免窗口开头的样本被整段误杀）；
    两个一字板量均 ≤ 首板量 ×0.1（极度缩量）。
    """
    if len(wave) != 3:
        return False
    first, second, third = wave
    if not (_is_one_word(second) and _is_one_word(third)):
        return False
    if pre_bar is not None and float(first.volume_shares) > float(
        pre_bar.volume_shares
    ) * _SANBANZU_FIRST_VS_PRE:
        return False
    return float(second.volume_shares) <= float(
        first.volume_shares
    ) * _SANBANZU_ONE_WORD_VS_FIRST and (
        float(third.volume_shares) <= float(first.volume_shares) * _SANBANZU_ONE_WORD_VS_FIRST
    )


def _has_suspect_day(bars: Sequence[Any]) -> bool:
    """是否存在 suspect 日（对齐旧项目 ``analyzer``：|递推涨跌幅| > 10.5%）。

    昨收取行上的 ``pre_close``（调用方应先经 :func:`_with_pre_close` 回填）；
    越界只可能是除权除息 / 送转 / 坏数据，整票拒收（readme §2.5）。
    昨收缺失时**跳过该日**（无法判定，不误杀）。
    """
    for bar in bars:
        pre = _to_float(getattr(bar, "pre_close", None))
        close = _to_float(getattr(bar, "close", None))
        if pre is None or close is None or pre <= 0:
            continue
        if abs(close / pre - 1) > _SUSPECT_PCT:
            return True
    return False


def _pct(price: float, pre_close: float) -> float | None:
    """相对昨收的涨幅（小数口径）。"""
    if pre_close <= 0:
        return None
    return price / pre_close - 1


def _day_metrics(bar: Any) -> DayMetrics:
    """由日线构造全天字段对象（昨收缺失则涨跌幅类字段留空）。"""
    base = _to_float(getattr(bar, "pre_close", None))
    pre_close = base if base is not None and base > 0 else None
    open_px = _to_float(getattr(bar, "open", None)) or 0.0
    high_px = _to_float(getattr(bar, "high", None)) or 0.0
    low_px = _to_float(getattr(bar, "low", None)) or 0.0
    close_px = _to_float(getattr(bar, "close", None)) or 0.0
    return DayMetrics(
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        open_pct=_pct(open_px, pre_close) if pre_close is not None else None,
        high_pct=_pct(high_px, pre_close) if pre_close is not None else None,
        low_pct=_pct(low_px, pre_close) if pre_close is not None else None,
        close_pct=_pct(close_px, pre_close) if pre_close is not None else None,
        amp_pct=(high_px - low_px) / pre_close if pre_close is not None else None,
        volume=_to_float(getattr(bar, "volume_shares", None)) or 0.0,
    )


def _minute_metrics(prices: Sequence[float], pre_close: float | None) -> MinuteMetrics:
    """由分钟价序列计算派生指标。"""
    if not prices or not pre_close or pre_close <= 0:
        return MinuteMetrics(n=len(prices))
    low = min(prices)
    high = max(prices)
    close = prices[-1]
    span = high - low
    return MinuteMetrics(
        n=len(prices),
        high_pct=high / pre_close - 1,
        low_pct=low / pre_close - 1,
        close_pct=close / pre_close - 1,
        amp_pct=span / pre_close,
        low_time_i=prices.index(low),
        close_pos=(close - low) / span if span > 0 else 0.5,
    )


def _mean(values: Sequence[float]) -> float | None:
    """算术平均；空序列返回 ``None``。"""
    return sum(values) / len(values) if values else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """安全比值；分母为 0/缺失时返回 ``None``。"""
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _is_main_board_code(code: str) -> bool:
    """是否 60/00 主板（排除创业板 30 / 科创板 68 / 北交所 8、4）。"""
    return code.startswith(("60", "00"))
