"""数据保留策略执行器（spec「数据库与仓储层」保留策略）。

| 表 | 保留策略 | 依据 |
| --- | --- | --- |
| ``raw_responses`` | 30 天 | 排障留档 |
| ``minute_bars``（分时） | 90 天 | 量最大、回溯价值随时间衰减 |
| ``news_flash``（快讯） | 7 天 | 对齐旧 quant（``save_news`` 同款 7 天） |
| ``themes`` / ``theme_stocks``（主题） | 7 个自然日 | 对齐旧 quant ``theme_snapshots`` |
| ``limit_up_pool``（涨停池） | 最近 30 个交易日 | 盘中轮询只留最新快照（覆盖写）， |
| | | 历史按最近 30 个交易日保留 |
| ``monitor_stocks``（监管名单） | 仅最新交易日 | 对齐旧 quant「cache + 最新兜底快照」 |
| ``daily_bars`` / ``advice_reports`` / ``ladder`` | 永久 | 日线为策略基础数据；天梯对齐旧 quant 无清理 |

由采集侧调度器在盘后窗口调用 :func:`run_retention`；在 SQLite（测试/本地）
上同样安全可执行。既可由调用方传入会话（DI），也可自行创建会话并提交。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.models.market import (
    LadderRow,
    LimitUpPool,
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

POOL_RETENTION_FALLBACK_DAYS = 45
"""涨停池历史交易日不足 :data:`POOL_RETENTION_TRADING_DAYS` 天时的自然日回退上限。

30 个交易日 ≈ 42 个自然日（含周末），取 45 留余量；仅用于「库里还没有 30 个
交易日」的早期阶段，避免按交易日判定因样本不足而误判。"""

PERMANENT_TABLES = ("daily_bars", "advice_reports", "ladder")
"""永久保留、清理任务 SHALL NOT 触碰的表（含连板天梯，对齐旧 quant）。"""


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
        ran_at: 执行时间（UTC）。
        raw_cutoff: 原始响应保留起点（早于该时间的被删除）。
        minute_bar_cutoff: 分时保留起点交易日（早于该交易日的被删除）。
        news_cutoff: 快讯保留起点时间。
        theme_cutoff: 主题保留起点交易日。
        pool_cutoff: 涨停池保留起点交易日。
    """

    raw_responses_deleted: int
    minute_bars_deleted: int
    news_deleted: int
    themes_deleted: int
    theme_stocks_deleted: int
    monitor_deleted: int
    pools_deleted: int
    ran_at: datetime
    raw_cutoff: datetime
    minute_bar_cutoff: date
    news_cutoff: datetime
    theme_cutoff: date
    pool_cutoff: date


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


async def _pool_retention_cutoff(session: AsyncSession, today: date) -> date:
    """涨停池保留起点（按**交易日**计数）。

    取库中倒序第 :data:`POOL_RETENTION_TRADING_DAYS` 个交易日作为起点，早于它的
    整日删除（``trade_date < cutoff`` 即清掉更老的交易日）。

    库中交易日不足该天数时（早期阶段）回退到
    :data:`POOL_RETENTION_FALLBACK_DAYS` 个自然日，避免因样本不足而误删。
    """
    stmt = (
        select(LimitUpPool.trade_date)
        .distinct()
        .order_by(LimitUpPool.trade_date.desc())
        .limit(POOL_RETENTION_TRADING_DAYS)
    )
    dates = list((await session.execute(stmt)).scalars().all())
    if len(dates) < POOL_RETENTION_TRADING_DAYS:
        return today - timedelta(days=POOL_RETENTION_FALLBACK_DAYS)
    return min(dates)


async def _execute(session: AsyncSession, *, commit: bool) -> RetentionReport:
    """在给定会话上执行删除，必要时提交。"""
    now = datetime.now(UTC)
    today = now.astimezone().date()
    raw_cutoff = now - timedelta(days=RAW_RESPONSE_RETENTION_DAYS)
    minute_cutoff = (now - timedelta(days=MINUTE_BAR_RETENTION_DAYS)).date()
    news_cutoff = now - timedelta(days=NEWS_RETENTION_DAYS)
    theme_cutoff = today - timedelta(days=THEME_RETENTION_DAYS)
    pool_cutoff = await _pool_retention_cutoff(session, today)

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

    # 监管名单：仅保留最新交易日（对齐旧 quant「cache + 最新兜底」语义）。
    monitor_deleted = 0
    latest_monitor = await session.scalar(select(func.max(MonitorStock.trade_date)))
    if latest_monitor is not None:
        monitor_deleted = await repo.delete_where(
            MonitorStock, MonitorStock.trade_date < latest_monitor
        )
    _ = LadderRow  # 天梯永久保留——显式引用以防误删（无操作）。

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
        ran_at=now,
        raw_cutoff=raw_cutoff,
        minute_bar_cutoff=minute_cutoff,
        news_cutoff=news_cutoff,
        theme_cutoff=theme_cutoff,
        pool_cutoff=pool_cutoff,
    )


__all__ = [
    "MINUTE_BAR_RETENTION_DAYS",
    "NEWS_RETENTION_DAYS",
    "PERMANENT_TABLES",
    "POOL_RETENTION_FALLBACK_DAYS",
    "POOL_RETENTION_TRADING_DAYS",
    "RAW_RESPONSE_RETENTION_DAYS",
    "RetentionReport",
    "THEME_RETENTION_DAYS",
    "run_retention",
]
