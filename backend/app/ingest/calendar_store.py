"""调度器的状态与日历存储（从 :mod:`app.ingest.scheduler` 拆出）。

三块职责，均复用 ``pool_snapshot`` 表（不新增 ORM 模型）：

- **交易日历**：取 ``trading_calendar`` 能力并缓存入库（``pool_name='trading_calendar'``）
  带 TTL；上游失败时回退「周一至五」并告警，绝不崩溃。
- **幂等状态**：当日「已完成任务」落库（``pool_name='ingest_state:<task>'``）——
  一次性任务成功后重启不重跑；失败**不**标记完成，窗口内重试。
- **每日维护**：保留策略清理 + 确保未来月分区存在。

``payload`` 承载结构化内容，``(trade_date, pool_name)`` 为幂等键。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Connection

from app.core.config import Settings
from app.db.partitions import PARTITIONED_TABLES, ensure_partitions_around_today
from app.db.session import get_engine
from app.ingest.pipeline import ProviderLike, contracts_from_raw, fetch_raw
from app.ingest.tasks import CALENDAR_POOL_NAME, WRITERS
from app.ingest.windows import load_calendar_ttl
from app.models.market import PoolSnapshot
from app.repositories import Repositories
from app.repositories.retention import RetentionReport, run_retention

__all__ = [
    "is_trading_day",
    "run_daily_maintenance",
]

logger = logging.getLogger(__name__)

_MAINTENANCE_KEY = "__daily_maintenance__"
_POOL_AMOUNT_KEY = "__pool_amount__"
_STATE_POOL_PREFIX = "ingest_state"


# ============================================================ 状态存储


def _state_pool_name(task_name: str) -> str:
    """任务状态在 ``pool_snapshot`` 中的池名。"""
    return f"{_STATE_POOL_PREFIX}:{task_name}"


async def _state_status(repos: Repositories, task_name: str, trade_date: date) -> str | None:
    """读取某任务某日的状态（``succeeded`` / ``failed`` / ``None``）。"""
    row = await repos.pool_snapshot.get(trade_date, _state_pool_name(task_name))
    if row is None:
        return None
    status = row.payload.get("status")
    return str(status) if status else None


async def _state_mark(
    repos: Repositories,
    task_name: str,
    trade_date: date,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """落库任务状态（按 ``(trade_date, pool_name)`` 幂等覆盖）。"""
    await repos.pool_snapshot.upsert_many(
        [
            {
                "trade_date": trade_date,
                "pool_name": _state_pool_name(task_name),
                "payload": {
                    "status": status,
                    "error": error,
                    "at": datetime.now(UTC).isoformat(),
                },
                "source": "scheduler",
            }
        ]
    )


# ============================================================ 交易日历


def _parse_dt(value: object) -> datetime | None:
    """解析 ISO 时间串为带时区 ``datetime``；非法返回 ``None``。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _calendar_dates(row: PoolSnapshot | None) -> set[date] | None:
    """从日历缓存行提取开市日期集合；无行返回 ``None``。"""
    if row is None:
        return None
    raw_dates = row.payload.get("dates")
    if not raw_dates:
        return set()
    return {date.fromisoformat(str(item)) for item in raw_dates}


def _calendar_fresh(row: PoolSnapshot | None, now: datetime, ttl: int) -> bool:
    """日历缓存是否在 TTL 内。"""
    if row is None:
        return False
    fetched = _parse_dt(row.payload.get("fetched_at"))
    if fetched is None:
        return False
    return (now - fetched).total_seconds() < ttl


async def _load_calendar(
    repos: Repositories,
    settings: Settings,
    trade_date: date,
    *,
    provider_override: ProviderLike | None,
    now: datetime,
    ttl: int,
) -> set[date] | None:
    """读缓存（TTL 内直接返回）；否则取数入库；失败返回陈旧缓存或 ``None``。"""
    row = await repos.pool_snapshot.get(trade_date, CALENDAR_POOL_NAME)
    cached = _calendar_dates(row)
    if cached and _calendar_fresh(row, now, ttl):
        return cached
    try:
        calendar_args = {"date": trade_date.isoformat()}
        raw = await fetch_raw(
            "trading_calendar",
            calendar_args,
            provider_override=provider_override,
        )
        contracts = contracts_from_raw(raw, calendar_args)
        await WRITERS["trading_calendar"](repos, contracts, trade_date, raw.source_id)
    except Exception as exc:
        logger.warning("交易日历获取失败（%s），按周一至五兜底", exc)
        return cached
    return _calendar_dates(await repos.pool_snapshot.get(trade_date, CALENDAR_POOL_NAME))


def _now_sh(settings: Settings) -> datetime:
    """当前上海时区时间。"""
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    return datetime.now(tz)


async def is_trading_day(
    repos: Repositories,
    settings: Settings,
    trade_date: date,
    *,
    provider_override: ProviderLike | None = None,
    now: datetime | None = None,
    ttl: int | None = None,
) -> bool:
    """交易日判定：日历覆盖内以日历为准；获取失败或未覆盖则按周一至五兜底。"""
    moment = now or _now_sh(settings)
    resolved_ttl = ttl if ttl is not None else load_calendar_ttl(settings)
    dates = await _load_calendar(
        repos,
        settings,
        trade_date,
        provider_override=provider_override,
        now=moment,
        ttl=resolved_ttl,
    )
    weekday = trade_date.weekday() < 5
    if not dates:
        return weekday
    if trade_date in dates:
        return True
    if trade_date > max(dates):
        logger.info(
            "交易日历未覆盖 %s（最远 %s），按周一至五兜底",
            trade_date.isoformat(),
            max(dates).isoformat(),
        )
        return weekday
    return False


# ============================================================ 每日维护


def _ensure_partitions(connection: Connection, table: str) -> list[str]:
    """确保指定表的「当前月 ± N 月」分区存在（PG 生效，其他方言跳过）。"""
    return ensure_partitions_around_today(connection, table)


async def run_daily_maintenance(repos: Repositories, settings: Settings) -> RetentionReport:
    """每日维护：执行保留策略清理 + 确保未来月分区存在（SQLite 跳过分区）。"""
    report = await run_retention(settings, session=repos.session)
    if not settings.is_sqlite:
        engine = get_engine(settings)
        async with engine.begin() as conn:
            for table in PARTITIONED_TABLES:
                await conn.run_sync(_ensure_partitions, table)
    return report
