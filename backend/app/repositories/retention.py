"""数据保留策略执行器（spec「数据库与仓储层」保留策略）。

| 表 | 保留策略 |
| --- | --- |
| ``raw_responses`` | 30 天 |
| ``std_minute_bars``（``minute_bars``） | 90 天 |
| ``std_daily_bars``（``daily_bars``） | 永久 |
| ``advice_reports`` | 永久 |

由采集侧调度器（Task 7）周期性调用 :func:`run_retention`；在 SQLite（测试/本地）
上同样安全可执行。既可由调用方传入会话（DI），也可自行创建会话并提交。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.models.market import MinuteBar
from app.models.raw import RawResponse
from app.repositories.market import MinuteBarRepository
from app.repositories.raw import RawResponseRepository

RAW_RESPONSE_RETENTION_DAYS = 30
"""``raw_responses`` 保留天数。"""

MINUTE_BAR_RETENTION_DAYS = 90
"""``minute_bars`` 保留天数。"""

PERMANENT_TABLES = ("daily_bars", "advice_reports")
"""永久保留、清理任务 SHALL NOT 触碰的表。"""


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """一次清理的结果。

    Attributes:
        raw_responses_deleted: 删除的原始响应行数。
        minute_bars_deleted: 删除的分时行数。
        ran_at: 执行时间（UTC）。
        raw_cutoff: 原始响应保留起点（早于该时间的被删除）。
        minute_bar_cutoff: 分时保留起点交易日（早于该交易日的被删除）。
    """

    raw_responses_deleted: int
    minute_bars_deleted: int
    ran_at: datetime
    raw_cutoff: datetime
    minute_bar_cutoff: date


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
    raw_cutoff = now - timedelta(days=RAW_RESPONSE_RETENTION_DAYS)
    minute_cutoff = (now - timedelta(days=MINUTE_BAR_RETENTION_DAYS)).date()

    raw_deleted = await RawResponseRepository(session).delete_where(
        RawResponse, RawResponse.fetched_at < raw_cutoff
    )
    minute_deleted = await MinuteBarRepository(session).delete_where(
        MinuteBar, MinuteBar.trade_date < minute_cutoff
    )
    if commit:
        await session.commit()

    return RetentionReport(
        raw_responses_deleted=raw_deleted,
        minute_bars_deleted=minute_deleted,
        ran_at=now,
        raw_cutoff=raw_cutoff,
        minute_bar_cutoff=minute_cutoff,
    )


__all__ = [
    "MINUTE_BAR_RETENTION_DAYS",
    "PERMANENT_TABLES",
    "RAW_RESPONSE_RETENTION_DAYS",
    "RetentionReport",
    "run_retention",
]
