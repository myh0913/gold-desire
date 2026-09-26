"""连板捉妖结构过滤（L1）——t11 回测口径的纯函数复刻。

口径来源：emotion-cycle ``scripts/t11_lianban_backtest.py::structure_filter``
（蓝本为 quant ``DailyBarAnalyzer``）。六道过滤按固定顺序执行，命中即拒：

1. 日线不足：窗口（截至信号日共 60 根）不足 10 根；
2. R0suspect：窗口内任一涨跌幅绝对值 > 10.5%（脏数据护栏，如除权未回填）；
3. 口径一致：日线窗口推导的当前连板波长度必须等于池表 ``continue_days``；
4. R1非第一波：当前波首板之前 20 个交易日内存在其他 ≥2 板波；
5. 无量基：首板前一日成交量缺失或为 0；
6. R2三板组 / R3量能萎缩 / R4首板量比 / R5位置过滤（见 ``structure_filter``）。

涨停判定口径：涨跌幅 ≥ 9.8%（主板近似）；一字板在涨停基础上要求开盘价与
最低价的涨幅均 ≥ 9.89%（对齐 quant ``limit_px × 0.999``）。

所有阈值（涨停 / 一字 / 结构窗口 / 最少根数 / 位置窗口）均可由调用方经
``Series`` / ``structure_filter`` 关键字参数覆写，模块常量仅为代码默认值
（由 L1 策略 params_schema 注入，保证阈值可插拔）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

#: 涨停阈值（小数）：涨跌幅达到该值视为涨停。
LIMIT_UP_PCT = 0.098

#: 一字板阈值（小数）：开盘价与最低价的涨幅均达到该值视为一字。
ONE_WORD_PCT = 0.0989

#: 数据质量护栏：窗口内任一涨跌幅绝对值超过该值 → 视为脏数据，整票放弃。
SUSPECT_PCT = 0.105

#: 结构窗口：截至信号日（含）回看的交易日根数。
STRUCTURE_WINDOW = 60

#: 最少日线根数：窗口不足该值直接拒。
MIN_WINDOW_BARS = 10

#: R5 位置过滤窗口：末 N 根的最低价（含信号日）。
POSITION_WINDOW = 30


@dataclass(frozen=True, slots=True)
class Bar:
    """结构窗口内的日线最小行（float 化，便于纯函数计算）。"""

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    pre_close: float | None = None


@dataclass(frozen=True, slots=True)
class StructureResult:
    """结构过滤结论（``ok=False`` 时 ``fail_reason`` 说明拒因）。"""

    ok: bool
    fail_reason: str
    vr: float | None = None
    pos: float | None = None


class Series:
    """日线序列：构造时推导涨跌幅 / 涨停 / 一字标记（t11 ``SeriesCtx`` 同款）。

    gold-desire 日线无 ``pct_chg`` 列，涨跌幅用 ``pre_close`` 回填链推导
    （缺失回填上一根收盘价；首根缺失 → ``None``，涨停判否处理）。
    """

    __slots__ = ("c", "dates", "h", "is_lu", "l", "o", "one_word", "pct", "vol")

    def __init__(
        self,
        bars: Sequence[Bar],
        *,
        limit_up_pct: float = LIMIT_UP_PCT,
        one_word_pct: float = ONE_WORD_PCT,
    ) -> None:
        """推导标记；涨停 / 一字阈值走参数（默认取模块常量）。"""

        self.dates: list[date] = [bar.trade_date for bar in bars]
        self.o = [bar.open for bar in bars]
        self.h = [bar.high for bar in bars]
        self.l = [bar.low for bar in bars]
        self.c = [bar.close for bar in bars]
        self.vol = [bar.volume for bar in bars]
        n = len(bars)
        pct: list[float | None] = []
        bases: list[float | None] = []
        prev_close: float | None = None
        for bar in bars:
            base = bar.pre_close if bar.pre_close else prev_close
            bases.append(base)
            pct.append(bar.close / base - 1 if base and base > 0 else None)
            prev_close = bar.close
        self.pct = pct
        self.is_lu: list[bool] = [p is not None and p >= limit_up_pct for p in pct]
        self.one_word: list[bool] = [False] * n
        for i in range(n):
            base = bases[i]
            if base and base > 0 and self.is_lu[i]:
                self.one_word[i] = (
                    self.o[i] / base - 1 >= one_word_pct and self.l[i] / base - 1 >= one_word_pct
                )

    def waves(self, lo: int, hi: int) -> list[tuple[int, int]]:
        """``[lo, hi]`` 内的连续涨停波（长度 ≥2 才算），返回 (起, 末) 索引对。"""
        out: list[tuple[int, int]] = []
        i = lo
        while i <= hi:
            if self.is_lu[i]:
                j = i
                while j + 1 <= hi and self.is_lu[j + 1]:
                    j += 1
                if j - i + 1 >= 2:
                    out.append((i, j))
                i = j + 1
            else:
                i += 1
        return out


def to_series(
    rows: Sequence[Any],
    *,
    limit_up_pct: float = LIMIT_UP_PCT,
    one_word_pct: float = ONE_WORD_PCT,
) -> Series:
    """ORM 日线行转 ``Series``（宽松 float 化，pre_close 缺失走回填链）。"""

    def _f(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    bars = [
        Bar(
            trade_date=row.trade_date,
            open=_f(row.open),
            high=_f(row.high),
            low=_f(row.low),
            close=_f(row.close),
            volume=_f(getattr(row, "volume_shares", None)),
            pre_close=_f(row.pre_close) if getattr(row, "pre_close", None) else None,
        )
        for row in rows
    ]
    return Series(bars, limit_up_pct=limit_up_pct, one_word_pct=one_word_pct)


def structure_filter(
    series: Series,
    i: int,
    boards: int,
    *,
    min_vr: float = 1.5,
    max_pos: float = 1.15,
    wave_lookback: int = 20,
    structure_window: int = STRUCTURE_WINDOW,
    min_window_bars: int = MIN_WINDOW_BARS,
    position_window: int = POSITION_WINDOW,
) -> StructureResult:
    """纯函数判定：信号日（``i``，通常为序列末根）是否通过六道结构过滤。

    与 t11 ``structure_filter`` 逐条对齐（``i`` 为 T-1 索引、``boards`` 为
    池表连板天数）；阈值 ``min_vr`` / ``max_pos`` / ``wave_lookback`` 与窗口
    ``structure_window`` / ``min_window_bars`` / ``position_window`` 走参数。
    """
    lo = max(0, i - structure_window + 1)
    if i - lo + 1 < min_window_bars:
        return StructureResult(False, "日线不足")
    for k in range(lo, i + 1):
        p = series.pct[k]
        if p is not None and abs(p) > SUSPECT_PCT:
            return StructureResult(False, "R0suspect")
    # 当前波：从 i 向前扩展连续涨停段
    s = i
    while s - 1 >= lo and series.is_lu[s - 1]:
        s -= 1
    cur_days = i - s + 1
    if cur_days != boards:
        return StructureResult(False, f"口径不一致(波{cur_days}≠池{boards})")
    for _ws, we in series.waves(lo, i):
        if we != i and s - we <= wave_lookback:
            return StructureResult(False, "R1非第一波")
    if s < 1 or not series.vol[s - 1]:
        return StructureResult(False, "无量基")
    base = series.vol[s - 1]
    if boards == 3 and (
        series.vol[s] <= base * 1.2
        and series.one_word[s + 1]
        and series.one_word[s + 2]
        and series.vol[s + 1] <= series.vol[s] * 0.1
        and series.vol[s + 2] <= series.vol[s] * 0.1
    ):
        return StructureResult(False, "R2三板组")
    if any(series.vol[k] < base for k in range(s, i + 1)):
        return StructureResult(False, "R3量能萎缩")
    vr = series.vol[s] / base
    if vr < min_vr:
        return StructureResult(False, "R4首板量比")
    min_low = min(series.l[max(lo, i - position_window + 1) : i + 1])
    if not min_low:
        return StructureResult(False, "R5无价")
    pos = series.o[s - 1] / min_low
    if pos >= max_pos:
        return StructureResult(False, "R5位置过滤")
    return StructureResult(True, "", vr=round(vr, 3), pos=round(pos, 3))
