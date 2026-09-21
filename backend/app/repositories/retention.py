"""数据保留策略执行器（spec「数据库与仓储层」保留策略）。

| 表 | 保留策略 | 依据 |
| --- | --- | --- |
| ``raw_responses`` | 30 天 | 排障留档 |
| ``minute_bars``（分时） | 90 天 | 量最大、回溯价值随时间衰减 |
| ``news_flash``（快讯） | 7 天 | 对齐旧 quant（``save_news`` 同款 7 天） |
| ``themes`` / ``theme_stocks``（主题） | 7 个自然日 | 对齐旧 quant ``theme_snapshots`` |
| ``limit_up_pool``（涨停池） | 90 天 | 旧 quant 仅留最新；本系统日线采集与样本推导 |
| | | 依赖 30 天池历史，故保留 90 天（有界且够用） |
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

POOL_RETENTION_DAYS = 90
"""``limit_up_pool`` 保留天数（覆盖 30 天候选推导窗口，有界增长）。"""

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


async def _execute(session: AsyncSession, *, commit: bool) -> RetentionReport:
    """在给定会话上执行删除，必要时提交。"""
    now = datetime.now(UTC)
    today = now.astimezone().date()
    raw_cutoff = now - timedelta(days=RAW_RESPONSE_RETENTION_DAYS)
    minute_cutoff = (now - timedelta(days=MINUTE_BAR_RETENTION_DAYS)).date()
    news_cutoff = now - timedelta(days=NEWS_RETENTION_DAYS)
    theme_cutoff = today - timedelta(days=THEME_RETENTION_DAYS)
    pool_cutoff = today - timedelta(days=POOL_RETENTION_DAYS)

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
    "POOL_RETENTION_DAYS",
    "RAW_RESPONSE_RETENTION_DAYS",
    "RetentionReport",
    "THEME_RETENTION_DAYS",
    "run_retention",
]
