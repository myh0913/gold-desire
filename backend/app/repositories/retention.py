"""数据保留策略执行器（spec「数据库与仓储层」保留策略）。

| 表 | 保留策略 | 依据 |
| --- | --- | --- |
| ``raw_responses`` | 30 天 | 排障留档 |
| ``minute_bars``（分时） | 90 天 | 量最大、回溯价值随时间衰减 |
| ``news_flash``（快讯） | 7 天 | 对齐旧 quant（``save_news`` 同款 7 天） |
| ``themes`` / ``theme_stocks``（主题） | 7 个自然日 | 对齐旧 quant ``theme_snapshots`` |
| ``limit_up_pool``（涨停池） | 最近 30 个交易日 | 盘中轮询只留最新快照（覆盖写）， |
| | | 历史按最近 30 个交易日保留 |
| ``ladder``（连板天梯） | 最近 1 年（365 自然日） | 用户 2026-09-22 决策；天梯页历史回看一年 |
| ``market_sentiment``（市场情绪） | 最近 30 个交易日 | 20 日走势图需要历史积累 |
| ``monitor_stocks``（监管名单） | 仅最新交易日 | 对齐旧 quant「cache + 最新兜底快照」 |
| ``daily_bars``（日线） | 最近 60 个交易日 | 用户 2026-09-22 决策；覆盖策略 30 天回看窗口 |
| ``advice_reports`` | 永久 | 建议记录为复盘基础数据 |

由采集侧调度器在盘后窗口调用 :func:`run_retention`；在 SQLite（测试/本地）
上同样安全可执行。既可由调用方传入会话（DI），也可自行创建会话并提交。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.models.market import (
    DailyBar,
    LadderRow,
    LimitUpPool,
    MarketSentiment,
    MinuteBar,
    MonitorStock,
    NewsFlash,
    Theme,
    ThemeStock,
)
from app.models.raw import RawResponse
from app.repositories.base import BaseRepository
from app.repositories.market import MinuteBarRepository
from app.repositories.raw import RawResponseRepository

RAW_RESPONSE_RETENTION_DAYS = 30
"""``raw_responses`` 保留天数。"""

MINUTE_BAR_RETENTION_DAYS = 90
"""``minute_bars`` 保留天数。"""

NEWS_RETENTION_DAYS = 7
"""``news_flash`` 保留天数（对齐旧 quant）。"""

THEME_RETENTION_DAYS = 7
"""``themes`` / ``theme_stocks`` 保留自然日数（对齐旧 quant）。"""

POOL_RETENTION_TRADING_DAYS = 30
"""``limit_up_pool`` 保留的最近**交易日**数（含当日）。

涨停池是盘中外呼的**快照**（库中只留最近一次拉取结果），历史按交易日计数保留，
供前端日期下拉与策略样本推导回看。更早的交易日整日清掉。
"""

LADDER_RETENTION_DAYS = 365
"""``ladder``（连板天梯）保留的**自然日**数——用户 2026-09-22 决策「最近一年」。

上游一次只给近 30 个交易日的矩阵，保留窗口放宽为一年后，更早数据不再因
上游窗口口径被误删（历史存量按自然日一年兜底）。
"""

DAILY_BAR_RETENTION_TRADING_DAYS = 60
"""``daily_bars``（日线）保留的最近**交易日**数——用户 2026-09-22 决策。

60 交易日（约 3 个自然月）完整覆盖建池/判定的 30 天回看窗口；更早日线
整日删除。**注意**：回测区间与因子有效性统计的起点不得早于该窗口。
"""

SENTIMENT_RETENTION_TRADING_DAYS = 30
"""``market_sentiment`` 保留的最近交易日数——总览 20 日走势图需要历史积累。"""

TRADING_DAY_FALLBACK_DAYS = 45
"""按交易日计数保留时，库中交易日不足目标天数所回退的自然日上限。"""

PERMANENT_TABLES = ("advice_reports",)
"""永久保留、清理任务 SHALL NOT 触碰的表（建议记录为复盘基础数据）。"""


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """一次清理的结果。

    Attributes:
        raw_responses_deleted: 删除的原始响应行数。
        minute_bars_deleted: 删除的分时行数。
        news_deleted: 删除的快讯行数。
        themes_deleted: 删除的主题榜行数。
        theme_stocks_deleted: 删除的主题成分股行数。
        monitor_deleted: 删除的监管名单行数（仅保留最新交易日）。
        pools_deleted: 删除的涨停池行数。
        ladder_deleted: 删除的连板天梯行数。
        sentiment_deleted: 删除的市场情绪行数。
        daily_bars_deleted: 删除的日线行数。
        ran_at: 执行时间（UTC）。
        raw_cutoff: 原始响应保留起点（早于该时间的被删除）。
        minute_bar_cutoff: 分时保留起点交易日（早于该交易日的被删除）。
        news_cutoff: 快讯保留起点时间。
        theme_cutoff: 主题保留起点交易日。
        pool_cutoff: 涨停池保留起点交易日。
        ladder_cutoff: 连板天梯保留起点日期（早于该日期的被删除）。
        sentiment_cutoff: 市场情绪保留起点交易日。
        daily_bar_cutoff: 日线保留起点交易日。
    """

    raw_responses_deleted: int
    minute_bars_deleted: int
    news_deleted: int
    themes_deleted: int
    theme_stocks_deleted: int
    monitor_deleted: int
    pools_deleted: int
    ladder_deleted: int
    sentiment_deleted: int
    daily_bars_deleted: int
    ran_at: datetime
    raw_cutoff: datetime
    minute_bar_cutoff: date
    news_cutoff: datetime
    theme_cutoff: date
    pool_cutoff: date
    ladder_cutoff: date
    sentiment_cutoff: date
    daily_bar_cutoff: date


async def run_retention(
    settings: Settings | None = None, *, session: AsyncSession | None = None
) -> RetentionReport:
    """执行保留策略，返回删除计数报告。

    Args:
        settings: 显式配置；缺省读全局配置。
        session: 复用的会话。传入时不提交（事务边界由调用方掌握）；
            不传时自行创建会话并在成功后提交。
    """
    resolved = settings or get_settings()
    if session is not None:
        return await _execute(session, commit=False)

    factory = get_session_factory(resolved)
    async with factory() as owned:
        return await _execute(owned, commit=True)


async def _trading_day_cutoff(
    session: AsyncSession, column: Any, keep_days: int, today: date
) -> date:
    """按**交易日**计数求保留起点（取库中倒序第 ``keep_days`` 个交易日）。

    早于该起点的整日删除（``trade_date < cutoff``）。库中交易日不足 ``keep_days``
    时（早期阶段）回退到 :data:`TRADING_DAY_FALLBACK_DAYS` 个自然日，避免因样本
    不足把仅有的数据误删。

    Args:
        session: 数据库会话。
        column: 目标表的 ``trade_date`` 列（如 ``LimitUpPool.trade_date``）。
        keep_days: 保留的交易日数。
        today: 当天日期（回退分支使用）。
    """
    stmt = select(column).distinct().order_by(column.desc()).limit(keep_days)
    dates = list((await session.execute(stmt)).scalars().all())
    if len(dates) < keep_days:
        return today - timedelta(days=TRADING_DAY_FALLBACK_DAYS)
    return min(dates)


async def _execute(session: AsyncSession, *, commit: bool) -> RetentionReport:
    """在给定会话上执行删除，必要时提交。"""
    now = datetime.now(UTC)
    today = now.astimezone().date()
    raw_cutoff = now - timedelta(days=RAW_RESPONSE_RETENTION_DAYS)
    minute_cutoff = (now - timedelta(days=MINUTE_BAR_RETENTION_DAYS)).date()
    news_cutoff = now - timedelta(days=NEWS_RETENTION_DAYS)
    theme_cutoff = today - timedelta(days=THEME_RETENTION_DAYS)
    pool_cutoff = await _trading_day_cutoff(
        session, LimitUpPool.trade_date, POOL_RETENTION_TRADING_DAYS, today
    )
    ladder_cutoff = today - timedelta(days=LADDER_RETENTION_DAYS)
    sentiment_cutoff = await _trading_day_cutoff(
        session, MarketSentiment.trade_date, SENTIMENT_RETENTION_TRADING_DAYS, today
    )
    daily_bar_cutoff = await _trading_day_cutoff(
        session, DailyBar.trade_date, DAILY_BAR_RETENTION_TRADING_DAYS, today
    )

    repo = BaseRepository(session)
    raw_deleted = await RawResponseRepository(session).delete_where(
        RawResponse, RawResponse.fetched_at < raw_cutoff
    )
    minute_deleted = await MinuteBarRepository(session).delete_where(
        MinuteBar, MinuteBar.trade_date < minute_cutoff
    )
    news_deleted = await repo.delete_where(NewsFlash, NewsFlash.ts < news_cutoff)
    themes_deleted = await repo.delete_where(Theme, Theme.trade_date < theme_cutoff)
    theme_stocks_deleted = await repo.delete_where(
        ThemeStock, ThemeStock.trade_date < theme_cutoff
    )
    pools_deleted = await repo.delete_where(LimitUpPool, LimitUpPool.trade_date < pool_cutoff)
    ladder_deleted = await repo.delete_where(LadderRow, LadderRow.trade_date < ladder_cutoff)
    sentiment_deleted = await repo.delete_where(
        MarketSentiment, MarketSentiment.trade_date < sentiment_cutoff
    )
    daily_bars_deleted = await repo.delete_where(DailyBar, DailyBar.trade_date < daily_bar_cutoff)

    # 监管名单：仅保留最新交易日（对齐旧 quant「cache + 最新兜底」语义）。
    monitor_deleted = 0
    latest_monitor = await session.scalar(select(func.max(MonitorStock.trade_date)))
    if latest_monitor is not None:
        monitor_deleted = await repo.delete_where(
            MonitorStock, MonitorStock.trade_date < latest_monitor
        )

    if commit:
        await session.commit()

    return RetentionReport(
        raw_responses_deleted=raw_deleted,
        minute_bars_deleted=minute_deleted,
        news_deleted=news_deleted,
        themes_deleted=themes_deleted,
        theme_stocks_deleted=theme_stocks_deleted,
        monitor_deleted=monitor_deleted,
        pools_deleted=pools_deleted,
        ladder_deleted=ladder_deleted,
        sentiment_deleted=sentiment_deleted,
        daily_bars_deleted=daily_bars_deleted,
        ran_at=now,
        raw_cutoff=raw_cutoff,
        minute_bar_cutoff=minute_cutoff,
        news_cutoff=news_cutoff,
        theme_cutoff=theme_cutoff,
        pool_cutoff=pool_cutoff,
        ladder_cutoff=ladder_cutoff,
        sentiment_cutoff=sentiment_cutoff,
        daily_bar_cutoff=daily_bar_cutoff,
    )


__all__ = [
    "DAILY_BAR_RETENTION_TRADING_DAYS",
    "LADDER_RETENTION_DAYS",
    "MINUTE_BAR_RETENTION_DAYS",
    "NEWS_RETENTION_DAYS",
    "PERMANENT_TABLES",
    "POOL_RETENTION_TRADING_DAYS",
    "RAW_RESPONSE_RETENTION_DAYS",
    "SENTIMENT_RETENTION_TRADING_DAYS",
    "THEME_RETENTION_DAYS",
    "TRADING_DAY_FALLBACK_DAYS",
    "RetentionReport",
    "run_retention",
]
