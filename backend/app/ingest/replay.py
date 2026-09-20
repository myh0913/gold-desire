"""快照回放防未来函数（spec-critical）。

回放/沙箱以历史日期 D 运行时，**只允许读库中 ``trade_date <= D`` 的快照**，禁止任何
实时上游调用（否则引入未来函数，污染回测）。本模块实现：

- :func:`replay_resolve`：DB 回放读取器，缺数据抛 :class:`SnapshotMissingError`；
- :func:`replay_scope`：上下文管理器，把读取器经
  :func:`app.datasources.resolve.set_replay_source` 挂到取数层，退出时恢复原钩子。

挂载后，任何 ``resolve(capability, replay_date=D, ...)`` 都会走本模块的 DB 读取器，
不触达 provider；若 ``trade_date <= D`` 无数据，则显式抛错并终止（SHALL NOT 用实时
数据补齐）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from typing import Any, cast

from sqlalchemy import select

import app.datasources.resolve as _resolve_module
from app.core.errors import SnapshotMissingError
from app.datasources.contracts import (
    ContractModel,
    DailyBarContract,
    LadderRowContract,
    LimitUpStockContract,
    MarketSentimentContract,
    NewsFlashContract,
    ThemeRankContract,
    ThemeStockContract,
    TradingDayContract,
)
from app.datasources.contracts.models import PoolType
from app.datasources.resolve import set_replay_source
from app.ingest.tasks import CALENDAR_POOL_NAME
from app.models.market import (
    DailyBar,
    LadderRow,
    LimitUpPool,
    MarketSentiment,
    NewsFlash,
    Theme,
    ThemeStock,
)
from app.repositories import Repositories

__all__ = ["replay_resolve", "replay_scope"]

logger = logging.getLogger(__name__)

_replay_repos: ContextVar[Repositories | None] = ContextVar("ingest_replay_repos", default=None)


# ============================================================ 行 → 契约


def _f(value: Any) -> float | None:
    """可选数值列 → float；NULL 保持 None（不用 0 顶替）。"""
    return None if value is None else float(value)


def _i(value: Any) -> int | None:
    """可选整数列 → int；NULL 保持 None（不用 0 顶替）。"""
    return None if value is None else int(value)


def _daily_bar(row: DailyBar) -> DailyBarContract:
    """ORM 日线行 → 契约。"""
    return DailyBarContract(
        code=row.code,
        trade_date=row.trade_date,
        open=float(row.open),
        high=float(row.high),
        low=float(row.low),
        close=float(row.close),
        pre_close=_f(row.pre_close),
        volume_shares=int(row.volume_shares),
        amount_yuan=float(row.amount_yuan),
    )


def _limit_up(row: LimitUpPool) -> LimitUpStockContract:
    """ORM 涨停池行 → 契约。"""
    return LimitUpStockContract(
        code=row.code,
        name=row.name,
        continue_days=int(row.continue_days),
        limit_up_time=row.limit_up_time,
        seal_amount_yuan=_f(row.seal_amount_yuan),
        open_times=_i(row.open_times),
        turnover_rate=_f(row.turnover_rate),
        amount_yuan=_f(row.amount_yuan),
        market_cap_yuan=_f(row.market_cap_yuan),
        pool_type=cast("PoolType", row.pool_type),
    )


def _ladder(row: LadderRow) -> LadderRowContract:
    """ORM 天梯行 → 契约。"""
    return LadderRowContract(
        trade_date=row.trade_date,
        code=row.code,
        name=row.name,
        continue_days=int(row.continue_days),
        first_seal_time=row.first_seal_time,
    )


def _sentiment(row: MarketSentiment) -> MarketSentimentContract:
    """ORM 情绪行 → 契约。"""
    return MarketSentimentContract(
        trade_date=row.trade_date,
        temperature=float(row.temperature),
        stage=row.stage,
        limit_up_count=int(row.limit_up_count),
        limit_down_count=int(row.limit_down_count),
        broken_board_count=int(row.broken_board_count),
        broken_rate=float(row.broken_rate),
        up_count=int(row.up_count),
        down_count=int(row.down_count),
        max_continue_days=_i(row.max_continue_days),
        premium_rate=float(row.premium_rate),
    )


def _theme(row: Theme) -> ThemeRankContract:
    """ORM 题材榜行 → 契约。"""
    return ThemeRankContract(
        trade_date=row.trade_date,
        rank=int(row.rank),
        name=row.name,
        core_avg_pct=_f(row.core_avg_pct),
        description=row.description,
        core_count=_i(row.core_count),
    )


def _theme_stock(row: ThemeStock) -> ThemeStockContract:
    """ORM 题材个股行 → 契约。"""
    return ThemeStockContract(
        trade_date=row.trade_date,
        theme_name=row.theme_name,
        code=row.code,
        name=row.name,
        price=float(row.price),
        pct=float(row.pct),
        turnover_rate=float(row.turnover_rate),
        continue_days=_i(row.continue_days),
    )


def _news(row: NewsFlash) -> NewsFlashContract:
    """ORM 快讯行 → 契约。"""
    return NewsFlashContract(
        ts=row.ts,
        level=row.level,
        title=row.title,
        summary=row.summary or "",
        symbols=list(row.symbols or []),
        categories=list(row.categories or []),
    )


# ============================================================ DB 回放读取


async def _read_daily_bars(
    repos: Repositories, trade_date: date, args: dict[str, Any]
) -> list[ContractModel]:
    stmt = select(DailyBar).where(DailyBar.trade_date <= trade_date)
    code = args.get("code")
    if code:
        stmt = stmt.where(DailyBar.code == str(code))
    stmt = stmt.order_by(DailyBar.trade_date, DailyBar.code)
    rows = (await repos.session.execute(stmt)).scalars().all()
    return [_daily_bar(row) for row in rows]


async def _read_limit_up_pool(
    repos: Repositories, trade_date: date, args: dict[str, Any]
) -> list[ContractModel]:
    stmt = select(LimitUpPool).where(LimitUpPool.trade_date <= trade_date)
    pool_type = args.get("pool_type")
    if pool_type:
        stmt = stmt.where(LimitUpPool.pool_type == str(pool_type))
    stmt = stmt.order_by(LimitUpPool.trade_date, LimitUpPool.continue_days.desc())
    rows = (await repos.session.execute(stmt)).scalars().all()
    return [_limit_up(row) for row in rows]


async def _read_ladder(
    repos: Repositories, trade_date: date, args: dict[str, Any]
) -> list[ContractModel]:
    stmt = select(LadderRow).where(LadderRow.trade_date <= trade_date)
    stmt = stmt.order_by(LadderRow.trade_date, LadderRow.continue_days.desc())
    rows = (await repos.session.execute(stmt)).scalars().all()
    return [_ladder(row) for row in rows]


async def _read_market_sentiment(
    repos: Repositories, trade_date: date, args: dict[str, Any]
) -> list[ContractModel]:
    stmt = (
        select(MarketSentiment)
        .where(MarketSentiment.trade_date <= trade_date)
        .order_by(MarketSentiment.trade_date)
    )
    rows = (await repos.session.execute(stmt)).scalars().all()
    return [_sentiment(row) for row in rows]


async def _read_theme_rank(
    repos: Repositories, trade_date: date, args: dict[str, Any]
) -> list[ContractModel]:
    stmt = (
        select(Theme).where(Theme.trade_date <= trade_date).order_by(Theme.trade_date, Theme.rank)
    )
    rows = (await repos.session.execute(stmt)).scalars().all()
    return [_theme(row) for row in rows]


async def _read_theme_stocks(
    repos: Repositories, trade_date: date, args: dict[str, Any]
) -> list[ContractModel]:
    stmt = select(ThemeStock).where(ThemeStock.trade_date <= trade_date)
    theme_name = args.get("theme_name")
    if theme_name:
        stmt = stmt.where(ThemeStock.theme_name == str(theme_name))
    stmt = stmt.order_by(ThemeStock.trade_date, ThemeStock.theme_name, ThemeStock.pct.desc())
    rows = (await repos.session.execute(stmt)).scalars().all()
    return [_theme_stock(row) for row in rows]


async def _read_newsflash(
    repos: Repositories, trade_date: date, args: dict[str, Any]
) -> list[ContractModel]:
    stmt = select(NewsFlash).order_by(NewsFlash.ts)
    rows = (await repos.session.execute(stmt)).scalars().all()
    return [_news(row) for row in rows if row.ts is not None and row.ts.date() <= trade_date]


async def _read_trading_calendar(
    repos: Repositories, trade_date: date, args: dict[str, Any]
) -> list[ContractModel]:
    row = await repos.pool_snapshot.get(trade_date, CALENDAR_POOL_NAME)
    if row is None:
        return []
    is_open = row.payload.get("is_open") or {}
    out: list[ContractModel] = []
    for iso, opened in is_open.items():
        day = date.fromisoformat(str(iso))
        if day <= trade_date:
            out.append(TradingDayContract(trade_date=day, is_open=bool(opened)))
    return out


_READERS: dict[str, Any] = {
    "daily_bars": _read_daily_bars,
    "limit_up_pool": _read_limit_up_pool,
    "ladder": _read_ladder,
    "market_sentiment": _read_market_sentiment,
    "theme_rank": _read_theme_rank,
    "theme_stocks": _read_theme_stocks,
    "newsflash": _read_newsflash,
    "trading_calendar": _read_trading_calendar,
}


async def replay_resolve(capability: str, trade_date: date, **args: Any) -> list[ContractModel]:
    """DB 回放读取器：只读 ``trade_date <= trade_date`` 的快照，绝不触达上游。

    Raises:
        SnapshotMissingError: 未在 :func:`replay_scope` 内，或所需数据缺失
            （错误信息点明能力/日期并声明禁止实时回退）。
    """
    repos = _replay_repos.get()
    if repos is None:
        raise SnapshotMissingError(
            f"回放读取器未安装：能力={capability} trade_date={trade_date.isoformat()}；"
            "请在 replay_scope(trade_date, repos) 内调用（禁止实时回退上游）",
            detail={"capability": capability, "trade_date": trade_date.isoformat()},
        )
    reader = _READERS.get(capability)
    if reader is None:
        raise SnapshotMissingError(
            f"回放不支持能力 {capability!r}（trade_date={trade_date.isoformat()}）",
            detail={"capability": capability, "trade_date": trade_date.isoformat()},
        )
    rows = await reader(repos, trade_date, args)
    if not rows:
        raise SnapshotMissingError(
            f"快照缺失：能力={capability} trade_date<={trade_date.isoformat()} 在库中无数据；"
            "回放模式禁止回退到实时数据源（防未来函数）",
            detail={"capability": capability, "trade_date": trade_date.isoformat()},
        )
    return rows


@contextmanager
def replay_scope(trade_date: date, repos: Repositories) -> Iterator[None]:
    """在作用域内安装 DB 回放读取器；退出时恢复此前的回放钩子。

    Args:
        trade_date: 回放基准日（读取器只返回 ``<= trade_date`` 的快照）。
        repos: 仓储容器（读取器经其会话访问数据库）。
    """
    token = _replay_repos.set(repos)
    previous = getattr(_resolve_module, "_replay_source", None)

    async def _reader(capability: str, *, replay_date: date, **args: Any) -> list[ContractModel]:
        return await replay_resolve(capability, replay_date, **args)

    set_replay_source(_reader)
    try:
        yield
    finally:
        set_replay_source(previous)
        _replay_repos.reset(token)
