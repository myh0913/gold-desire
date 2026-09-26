"""S1 首板低吸的**纯信号计算部件**（无 I/O）。

从 :mod:`app.strategies.plugins.firstboard_dip.strategy` 拆出（规范：单文件
≤500 行）。日线最小行 ``_Bar``、昨收回填、涨跌幅 / 涨停判定与「低位横盘首板」
信号判定（``_firstboard_signal``，口径对齐回测脚本 ``s1_firstboard``）都在这里；
I/O 编排与提醒落库见同目录 ``strategy.py``。

模块级常量 ``_SUSPECT_PCT`` 是**数据质量护栏**（窗口内涨跌幅越界视为脏数据，
如除权未回填），不是策略阈值——所有策略阈值走 ``params_schema``。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

#: 数据质量护栏：窗口内任一涨跌幅绝对值超过该值 → 视为脏数据，整票放弃。
_SUSPECT_PCT = 0.105


@dataclass(frozen=True, slots=True)
class _Bar:
    """扫描窗口内的日线最小行（float 化，便于纯函数计算）。"""

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    pre_close: float | None = None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """盘后扫描产出的首板候选。"""

    code: str
    name: str
    first_board_date: date
    board_close: float
    pos_60d: float


def _to_float(value: Any) -> float | None:
    """宽松转 float（ORM 行可能给出 Decimal / str / None）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _backfill_pre_close(bars: list[_Bar]) -> list[_Bar]:
    """用上一根收盘价回填缺失的 ``pre_close``。

    与 ``dragon_bars._with_pre_close`` 同款口径：数据源的 ``pre_close``
    经常缺失，回填后才能计算涨跌幅；首根无法回填 → 保持 ``None``
    （涨跌幅判否处理）。
    """
    filled: list[_Bar] = []
    prev_close: float | None = None
    for bar in bars:
        pre_close = bar.pre_close if bar.pre_close else prev_close
        filled.append(replace(bar, pre_close=pre_close))
        prev_close = bar.close
    return filled


def _pct_chg(bar: _Bar) -> float | None:
    """当日涨跌幅（小数）；``pre_close`` 缺失时返回 ``None``。"""
    if bar.pre_close is None or bar.pre_close <= 0:
        return None
    return bar.close / bar.pre_close - 1


def _is_main_board_code(code: str) -> bool:
    """主板代码前缀：沪市 60 / 深市 00。"""
    return code.startswith(("60", "00"))


def _firstboard_signal(
    bars: list[_Bar],
    *,
    limit_up_pct: float,
    no_board_days: int,
    pos_window: int,
    pos_max: float,
) -> dict[str, Any] | None:
    """纯函数判定：末根是否为"低位横盘首板"。

    判定链（与回测口径 ``s1_firstboard`` 一致）：

    1. 数据质量护栏：窗口内任一涨跌幅绝对值 > ``_SUSPECT_PCT`` → 放弃；
    2. 末根涨停、且前一根非涨停（连板不是首板）；
    3. 板前 ``no_board_days`` 个交易日内无板（横盘确认）；
    4. 板前 ``pos_window`` 根 + 板日共 ``pos_window + 1`` 根窗口内，
       收盘位置分位 ``(close - low) / (high - low)`` 不超过 ``pos_max``。
    """
    pcts: list[float | None] = [_pct_chg(bar) for bar in bars]
    if any(p is not None and abs(p) > _SUSPECT_PCT for p in pcts):
        return None
    is_lu: list[bool] = [p is not None and p >= limit_up_pct for p in pcts]
    b = len(bars) - 1
    if b < 1 or not is_lu[b] or is_lu[b - 1]:
        return None
    if b < pos_window:
        return None
    if any(is_lu[k] for k in range(b - no_board_days, b)):
        return None
    window = bars[b - pos_window : b + 1]
    hi = max(bar.high for bar in window)
    lo = min(bar.low for bar in window)
    if hi <= lo:
        return None
    pos = (window[-1].close - lo) / (hi - lo)
    if pos > pos_max:
        return None
    return {
        "first_board_date": bars[b].trade_date,
        "board_close": bars[b].close,
        "pos_60d": round(pos, 4),
    }
