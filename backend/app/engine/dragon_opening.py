"""龙回头样本的**09:25 开盘注入路径**（``Phase.OPENING`` 盘中判定用）。

从 :mod:`app.engine.dragon_samples` 拆出（规范：单文件 ≤500 行）。
:func:`build_opening_samples` 在 D+1 / D+2 日线尚未入库时，以 09:25 撮合价
合成 T / T1 开盘字段，分别产出 S2（D=上一交易日）与 S4（D=上上交易日）
两个视角的样本；撮合价相对最近收盘越界（除权伪跳变 / 坏数据）的票拒收
（:func:`app.engine.guards.is_suspect_price_move`）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from app.engine.dragon_bars import _has_suspect_day, _is_limit_up, _is_main_board_code
from app.engine.dragon_model import DayMetrics, DragonSample
from app.engine.dragon_samples import (
    _dates_adjacent,
    _load_minutes,
    _load_trading_dates,
    _sample_from_bars,
)
from app.engine.guards import is_suspect_price_move

__all__ = [
    "build_opening_samples",
]


def _wave_start_for(
    bars: Sequence[Any],
    index: int,
    trading_dates: set[date] | None,
) -> tuple[int, Any | None] | None:
    """定位 ``bars[index]``（首阴）紧邻连板波的起点与波前一日。

    返回 ``(wave_start, pre_wave_bar)``；首阴与波末板不相邻（隔缺失交易日）
    或不构成连板时返回 ``None``。判定口径与 :func:`app.engine.dragon_samples._scan_code`
    一致。
    """
    limit_flags = [_is_limit_up(bar) for bar in bars]
    consecutive = [False] * len(bars)
    for i in range(1, len(bars)):
        consecutive[i] = _dates_adjacent(bars[i - 1].trade_date, bars[i].trade_date, trading_dates)
    if index < 1 or not consecutive[index]:
        return None
    if not float(bars[index].close) < float(bars[index].open):
        return None
    wave_start = index - 1
    while (
        wave_start >= 0
        and limit_flags[wave_start]
        and (wave_start == index - 1 or consecutive[wave_start + 1])
    ):
        wave_start -= 1
    wave_start += 1
    pre_wave_bar = bars[wave_start - 1] if wave_start > 0 and consecutive[wave_start] else None
    return wave_start, pre_wave_bar


async def build_opening_samples(
    repos: Any,
    today: date,
    opening_prices: Mapping[str, float],
) -> list[DragonSample]:
    """构造 **09:25 开盘注入版**样本（策略 ``Phase.OPENING`` 盘中判定用）。

    与 :func:`app.engine.dragon_samples.build_samples` 的差异：D+1 / D+2 日线尚未
    入库，当日开盘价由 ``opening_prices``（09:25 撮合，``{code: price}``）合成注入：

    - **S2 视角**：``D = 上一交易日``（日线/分时已齐），``t.open`` = 今日撮合价、
      ``t.open_pct`` = 撮合价 / D 收盘 - 1；
    - **S4 视角**：``D = 上上交易日``（``t`` = 上一交易日全天日线已齐），
      ``t1.open`` / ``t1.open_pct`` = 今日撮合价注入。

    ``T1``（S2 的可卖日）取日历中今日之后的下一交易日；日历未覆盖时以今日占位
    （仅影响展示，卖出撮合由盘后 INTRADAY 阶段以真实日线完成）。

    Returns:
        按 ``(D, code)`` 升序的样本列表；日历缺失或历史不足两个交易日时返回空。
        撮合价相对最近收盘越界（|变动| > 10.5%，除权伪跳变 / 坏数据）的票不产出。
    """
    trading_dates = await _load_trading_dates(
        repos, today - timedelta(days=45), today + timedelta(days=15), anchor=today
    )
    if not trading_dates:
        return []
    past = sorted(day for day in trading_dates if day < today)
    future = sorted(day for day in trading_dates if day > today)
    if len(past) < 2:
        return []
    y1, y2 = past[-1], past[-2]
    t1_for_s2 = future[0] if future else today

    out: list[DragonSample] = []
    for stock in await repos.stocks.list_all(board="主板"):
        if bool(getattr(stock, "is_st", False)):
            continue
        code = str(stock.code)
        if not _is_main_board_code(code):
            continue
        om_price = opening_prices.get(code)
        if om_price is None or om_price <= 0:
            continue
        bars = await repos.daily_bars.get_range(code, y2 - timedelta(days=45), y1)
        if not bars or _has_suspect_day(bars):
            continue
        # 撮合价 vs 最近收盘（bars 升序，末根即有效昨收）：越界 = 除权伪跳变 /
        # 坏数据（库内日线与盘中撮合价复权口径未对齐），整票拒收。
        if is_suspect_price_move(om_price, float(bars[-1].close)):
            continue
        minute_by_date = await _load_minutes(repos, code, bars)
        today_rows = await repos.minute_bars.get_day(code, today)
        if today_rows:
            minute_by_date[today] = tuple(
                float(row.price)
                for row in sorted(today_rows, key=lambda row: int(row.minute_index))
            )

        kwargs: dict[str, Any] = {
            "bars": bars,
            "minute_by_date": minute_by_date,
            "trading_dates": trading_dates,
            "om_price": om_price,
            "code": code,
            "name": str(stock.name),
            "today": today,
            "t1_for_s2": t1_for_s2,
        }
        s2 = _opening_sample(d_date=y1, for_s4=False, **kwargs)
        if s2 is not None and not s2.is_sanbanzu:
            out.append(s2)
        s4 = _opening_sample(d_date=y2, for_s4=True, **kwargs)
        if s4 is not None and not s4.is_sanbanzu:
            out.append(s4)

    out.sort(key=lambda sample: (sample.D, sample.code))
    return out


def _opening_sample(
    *,
    bars: Sequence[Any],
    minute_by_date: Mapping[date, tuple[float, ...]],
    trading_dates: set[date] | None,
    d_date: date,
    om_price: float,
    code: str,
    name: str,
    today: date,
    t1_for_s2: date,
    for_s4: bool,
) -> DragonSample | None:
    """由历史日线 + 撮合价合成一条开盘注入样本（S2 / S4 双视角共用）。"""
    index = next((i for i, bar in enumerate(bars) if bar.trade_date == d_date), None)
    if index is None:
        return None
    located = _wave_start_for(bars, index, trading_dates)
    if located is None:
        return None
    wave_start, pre_wave_bar = located
    wave = bars[wave_start:index]
    if len(wave) < 2:
        return None
    d_close = float(bars[index].close)
    open_pct = om_price / d_close - 1 if d_close > 0 else None
    if for_s4:
        # t = D+1（上一交易日）真实日线；t1 = 今日（撮合价合成）。
        return _sample_from_bars(
            code,
            name,
            bars,
            minute_by_date,
            index,
            wave_start,
            pre_wave_bar,
            t1_metrics=DayMetrics(open=om_price, open_pct=open_pct),
            t1_date=today,
        )
    # S2：t = 今日（撮合价合成）；t1 = 下一交易日（占位）。
    return _sample_from_bars(
        code,
        name,
        bars,
        minute_by_date,
        index,
        wave_start,
        pre_wave_bar,
        t_metrics=DayMetrics(open=om_price, open_pct=open_pct),
        t1_metrics=DayMetrics(),
        t_date=today,
        t1_date=t1_for_s2,
    )
