"""L1 连板捉妖的**盘后扫描与竞价读取**（I/O 编排）。

从 :mod:`app.strategies.plugins.lianban.strategy` 拆出（规范：单文件
≤500 行）。涨停池候选扫描（``scan_candidates``，六道结构过滤在此调用）、
竞价环境否决计数（``auction_env``）、交易日历 / 集合竞价快照读取
（``prev_trading_day`` / ``opening_prices``）都在这里；阈值由策略层解析后
显式传入，本模块不做参数回退。六道过滤判定见同目录 ``filters.py``。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.strategies.context import StrategyContext
from app.strategies.plugins.lianban.filters import (
    StructureResult,
    structure_filter,
    to_series,
)

#: 盘后快照池名：交易日历（与 dragon / firstboard_dip 共用同一种子格式）。
_CALENDAR_POOL_NAME = "trading_calendar"

#: 交易日历探测回看的自然日跨度（足够覆盖 60 个交易日窗口）。
_CALENDAR_LOOKBACK_DAYS = 45

#: 涨停池池型：主池。
_POOL_TYPE = "limit_up"

#: 允许的连板天数：2 板 / 3 板。
_BOARDS_ALLOWED = frozenset({2, 3})


@dataclass(frozen=True, slots=True)
class _Candidate:
    """盘后扫描产出的连板候选（结构过滤全部通过）。"""

    code: str
    name: str
    boards: int
    signal_day: date
    prev_close: float
    vr: float
    pos: float
    float_mv_yuan: float | None = None


def _to_float(value: Any) -> float | None:
    """宽松转 float（ORM 行可能给出 Decimal / str / None）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_main_board_code(code: str) -> bool:
    """主板代码前缀：沪市 60 / 深市 00。"""
    return code.startswith(("60", "00"))


def _is_st_name(name: str) -> bool:
    """名称前缀 ST 判定（stocks 主档缺失时的兜底）。"""
    return str(name).upper().startswith(("ST", "*ST"))


async def scan_candidates(
    ctx: StrategyContext,
    *,
    signal_day: date,
    min_vr: float,
    max_pos: float,
    wave_lookback: int,
    limit_up_pct: float,
    one_word_pct: float,
    structure_window: int,
    min_window_bars: int,
    position_window: int,
) -> list[_Candidate]:
    """扫描 signal_day 涨停池中的连板候选（按代码排序保证输出稳定）。"""
    repos = ctx.repos
    if repos is None:
        return []
    # 窗口根数约合 1.8 倍自然日 + 节假日缓冲
    start = signal_day - timedelta(days=int(structure_window * 1.8) + 15)
    stocks = {str(item.code): item for item in await repos.stocks.list_all()}
    candidates: list[_Candidate] = []
    for row in await repos.limit_up_pool.get_pool(signal_day, _POOL_TYPE):
        boards = int(row.continue_days)
        if boards not in _BOARDS_ALLOWED:
            continue
        code = str(row.code)
        if not _is_main_board_code(code):
            continue
        stock = stocks.get(code)
        if stock is not None:
            if bool(getattr(stock, "is_st", False)):
                continue
        elif _is_st_name(str(row.name)):
            continue
        rows = await repos.daily_bars.get_range(code, start, signal_day)
        if not rows or rows[-1].trade_date != signal_day:
            continue  # 停牌 / 数据缺口
        series = to_series(rows, limit_up_pct=limit_up_pct, one_word_pct=one_word_pct)
        result: StructureResult = structure_filter(
            series,
            len(series.dates) - 1,
            boards,
            min_vr=min_vr,
            max_pos=max_pos,
            wave_lookback=wave_lookback,
            structure_window=structure_window,
            min_window_bars=min_window_bars,
            position_window=position_window,
        )
        if not result.ok or result.vr is None or result.pos is None:
            continue
        prev_close = _to_float(rows[-1].close)
        if prev_close is None or prev_close <= 0:
            continue
        candidates.append(
            _Candidate(
                code=code,
                name=str(row.name),
                boards=boards,
                signal_day=signal_day,
                prev_close=prev_close,
                vr=result.vr,
                pos=result.pos,
                float_mv_yuan=_to_float(getattr(row, "free_cap_yuan", None)),
            )
        )
    candidates.sort(key=lambda item: item.code)
    return candidates


async def auction_env(
    ctx: StrategyContext,
    *,
    signal_day: date,
    prices: Mapping[str, float],
    threshold: float,
    limit: int,
) -> tuple[bool, int]:
    """环境否决：T-1 池全部 boards≥2 票的竞价跌停家数（t11 口径）。

    不限板型 / ST；停牌或未参与竞价撮合的票缺失不计。
    返回 ``(是否否决, 跌停家数)``。
    """
    repos = ctx.repos
    if repos is None:
        return False, 0
    rows = await repos.limit_up_pool.get_by_date(signal_day)
    codes = sorted({str(row.code) for row in rows if int(row.continue_days) >= 2})
    count = 0
    for code in codes:
        price = prices.get(code)
        if price is None or price <= 0:
            continue
        bars = await repos.daily_bars.get_range(code, signal_day, signal_day)
        if not bars:
            continue
        prev_close = _to_float(bars[-1].close)
        if prev_close is None or prev_close <= 0:
            continue
        if price / prev_close - 1 <= threshold:
            count += 1
    return count > limit, count


async def prev_trading_day(ctx: StrategyContext) -> date | None:
    """从交易日历快照解析 ctx.trade_date 的前一交易日（缺失不臆断）。"""
    repos = ctx.repos
    if repos is None:
        return None
    probe_dates = [
        ctx.trade_date,
        ctx.trade_date - timedelta(days=_CALENDAR_LOOKBACK_DAYS),
    ]
    for probe in probe_dates:
        snapshot = await repos.pool_snapshot.get(probe, _CALENDAR_POOL_NAME)
        payload = snapshot.payload if snapshot is not None else None
        if not isinstance(payload, dict):
            continue
        try:
            dates = [date.fromisoformat(str(item)) for item in payload.get("dates", [])]
        except ValueError:
            continue
        past = sorted(d for d in dates if d < ctx.trade_date)
        if past:
            return past[-1]
    return None


async def opening_prices(ctx: StrategyContext) -> dict[str, float]:
    """读取当日集合竞价撮合快照：{code: 开盘价}。"""
    repos = ctx.repos
    if repos is None:
        return {}
    snapshot = await repos.pool_snapshot.get(ctx.trade_date, "opening_match")
    payload = snapshot.payload if snapshot is not None else None
    if not isinstance(payload, dict):
        return {}
    return {
        str(code): float(item["price"])
        for code, item in payload.items()
        if isinstance(item, dict) and item.get("price")
    }
