"""常驻采集调度器。

职责：

- **交易日历**：取 ``trading_calendar`` 能力并缓存入库（复用 ``pool_snapshot``，
  ``pool_name='trading_calendar'``）带 TTL；上游失败时回退「周一至五」并告警，绝不崩溃。
- **窗口**：竞价/盘中/尾盘/盘后四窗口，纯数据（:mod:`app.ingest.windows`），可配置。
- **幂等状态**：当日「已完成任务」落库（复用 ``pool_snapshot``，
  ``pool_name='ingest_state:<task>'``）——重启不重跑；失败**不**标记完成，窗口内重试。
- **tick 循环**：可配置间隔，SIGTERM/SIGINT 优雅退出。
- **每日维护**：盘后调用 :func:`app.repositories.retention.run_retention` 并确保未来月分区存在。

入口：``python -m app.ingest.scheduler``（常驻）/ ``--once [WINDOW]``（单跑）。

状态与日历均复用 ``pool_snapshot`` 表（本 Task 不新增 ORM 模型）：
``payload`` 承载结构化内容，``(trade_date, pool_name)`` 为幂等键。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.partitions import PARTITIONED_TABLES, ensure_partitions_around_today
from app.db.session import get_engine, get_session_factory
from app.ingest.pipeline import IngestResult, ProviderLike, contracts_from_raw, fetch_raw, run_task
from app.ingest.tasks import CALENDAR_POOL_NAME, WRITERS, IngestTaskDef, all_tasks
from app.ingest.windows import (
    Window,
    in_window,
    load_calendar_ttl,
    load_tick_seconds,
    load_windows,
    window_by_name,
)
from app.models.market import PoolSnapshot
from app.repositories import Repositories
from app.repositories.retention import RetentionReport, run_retention

__all__ = [
    "IngestScheduler",
    "is_trading_day",
    "main",
    "run_daily_maintenance",
]

logger = logging.getLogger(__name__)

_MAINTENANCE_KEY = "__daily_maintenance__"
_STATE_POOL_PREFIX = "ingest_state"


# ============================================================ 状态 / 日历存储


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


# ============================================================ 调度器


class IngestScheduler:
    """常驻采集调度器。

    Args:
        settings: 运行配置。
        session_factory: 会话工厂（测试注入）；缺省用全局工厂。
        provider_override: 注入 provider（测试/手动）；缺省走注册表主备链。
        now_fn: 时钟（测试注入）；缺省上海时区当前时间。
        sleep_fn: 休眠函数（测试注入）；缺省 ``asyncio.sleep``。
        windows: 窗口列表；缺省 :func:`load_windows`。
        tick_seconds: tick 间隔；缺省 :func:`load_tick_seconds`。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        provider_override: ProviderLike | None = None,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        windows: list[Window] | None = None,
        tick_seconds: int | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._session_factory = session_factory
        self._provider_override = provider_override
        self._now = now_fn or (lambda: _now_sh(self.settings))
        self._sleep = sleep_fn or asyncio.sleep
        self._windows = windows if windows is not None else load_windows(self.settings)
        self.tick_seconds = (
            tick_seconds if tick_seconds is not None else load_tick_seconds(self.settings)
        )
        self._calendar_ttl = load_calendar_ttl(self.settings)
        self._stopping = False
        self._last_attempt: dict[tuple[str, date], datetime] = {}

    # ---------------------------------------------------------------- 窗口

    def _window(self, name: str) -> Window:
        """按名取配置窗口。"""
        return window_by_name(self._windows, name)

    def _select_tasks(self, window: str | Window | None, now: datetime) -> list[IngestTaskDef]:
        """选出本轮应执行的任务（启用 + 窗口到期/匹配）。"""
        tasks = [item for item in all_tasks() if item.enabled]
        if window is None:
            selected: list[IngestTaskDef] = []
            for item in tasks:
                if item.window is None or in_window(now, self._window(item.window.name)):
                    selected.append(item)
            return selected
        name = window.name if isinstance(window, Window) else str(window)
        return [item for item in tasks if item.window is not None and item.window.name == name]

    def _interval_due(self, defn: IngestTaskDef, trade_date: date, now: datetime) -> bool:
        """按 interval 判定是否到期（``interval<=0`` 视为窗口内只跑一次，恒到期）。"""
        if defn.interval_seconds <= 0:
            return True
        last = self._last_attempt.get((defn.name, trade_date))
        if last is None:
            return True
        return (now - last).total_seconds() >= defn.interval_seconds

    # ------------------------------------------------------------ 单轮调度

    async def run_once(
        self,
        window: str | Window | None = None,
        *,
        now: datetime | None = None,
        trade_date: date | None = None,
    ) -> list[IngestResult]:
        """执行一轮调度检查，返回本轮实际执行的结果列表。

        Args:
            window: 限定窗口（名或 :class:`Window`）；``None`` 表示按当前时间自动判定。
            now: 当前时间（测试注入）。
            trade_date: 目标交易日；缺省取 ``now`` 的日期。
        """
        moment = now or self._now()
        target_date = trade_date or moment.date()
        factory = self._session_factory or get_session_factory(self.settings)
        executed: list[IngestResult] = []

        async with factory() as session:
            repos = Repositories.build(session)
            trading = await is_trading_day(
                repos,
                self.settings,
                target_date,
                provider_override=self._provider_override,
                now=moment,
                ttl=self._calendar_ttl,
            )
            await session.commit()
            if not trading:
                logger.info(
                    "ingest_skip_non_trading_day", extra={"trade_date": target_date.isoformat()}
                )
                return executed

            for defn in self._select_tasks(window, moment):
                if await _state_status(repos, defn.name, target_date) == "succeeded":
                    continue
                if not self._interval_due(defn, target_date, moment):
                    continue
                result = await run_task(
                    defn,
                    target_date,
                    repos=repos,
                    settings=self.settings,
                    provider_override=self._provider_override,
                )
                self._last_attempt[(defn.name, target_date)] = moment
                await _state_mark(repos, defn.name, target_date, result.status, error=result.error)
                await session.commit()
                executed.append(result)

            await self._maybe_maintenance(repos, session, target_date, moment, window)
            await self._maybe_strategies(repos, target_date, moment, window)
            await session.commit()
        return executed

    async def _maybe_strategies(
        self,
        repos: Repositories,
        trade_date: date,
        now: datetime,
        window: str | Window | None,
    ) -> None:
        """盘后窗口内执行一次策略阶段（POOL + INTRADAY；幂等见 strategy_hooks）。

        策略阶段依赖**完整日线/分时**（dragon 样本要求 D+1/D+2 已入库），
        故挂在 postmarket 采集之后而非盘中——产出为确认记录，供建议/复盘消费。
        """
        from app.ingest.strategy_hooks import run_strategy_phases

        if window is not None:
            name = window.name if isinstance(window, Window) else str(window)
            if name != "postmarket":
                return
        elif not in_window(now, self._window("postmarket")):
            return
        try:
            await run_strategy_phases(repos, self.settings, trade_date)
        except Exception:
            logger.exception(
                "strategy_phases_failed", extra={"trade_date": trade_date.isoformat()}
            )

    async def _maybe_maintenance(
        self,
        repos: Repositories,
        session: AsyncSession,
        trade_date: date,
        now: datetime,
        window: str | Window | None,
    ) -> None:
        """盘后窗口内执行一次每日维护（成功才标记完成）。"""
        if window is not None:
            name = window.name if isinstance(window, Window) else str(window)
            if name != "postmarket":
                return
        elif not in_window(now, self._window("postmarket")):
            return
        if await _state_status(repos, _MAINTENANCE_KEY, trade_date) == "succeeded":
            return
        try:
            report = await run_daily_maintenance(repos, self.settings)
            await _state_mark(repos, _MAINTENANCE_KEY, trade_date, "succeeded")
            logger.info(
                "ingest_maintenance_done",
                extra={
                    "trade_date": trade_date.isoformat(),
                    "raw_deleted": report.raw_responses_deleted,
                    "minute_deleted": report.minute_bars_deleted,
                },
            )
        except Exception as exc:
            logger.exception(
                "ingest_maintenance_failed",
                extra={"trade_date": trade_date.isoformat(), "error": str(exc)},
            )
            await _state_mark(repos, _MAINTENANCE_KEY, trade_date, "failed")
        await session.commit()

    # ------------------------------------------------------------ 常驻循环

    def stop(self) -> None:
        """请求优雅退出。"""
        self._stopping = True

    def _install_signal_handlers(self) -> None:
        """注册 SIGTERM/SIGINT 处理（非主线程或不支持时跳过）。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.stop)
            except (NotImplementedError, RuntimeError):
                continue

    async def run_forever(self) -> None:
        """常驻 tick 循环，直到 :meth:`stop` 或收到 SIGTERM/SIGINT。"""
        self._install_signal_handlers()
        logger.info("ingest_scheduler_started", extra={"tick_seconds": self.tick_seconds})
        while not self._stopping:
            try:
                await self.run_once()
            except Exception:
                logger.exception("ingest_tick_failed")
            if self._stopping:
                break
            await self._sleep(self.tick_seconds)
        logger.info("ingest_scheduler_stopped")


# ============================================================ CLI


def _ensure_schema(settings: Any) -> None:
    """启动前确保表结构存在（PG 用 alembic 迁移；SQLite/未迁移库用 metadata.create_all 兜底）。

    设计：调度器是独立进程，未必与 API 共享 `alembic upgrade head` 的执行时机。
    本函数对所有声明的 ORM 模型跑一次 ``Base.metadata.create_all``，幂等、不会破坏迁移版本。
    生产 PostgreSQL 部署仍以 alembic 为准；本函数仅是"裸跑 SQLite 时不会因缺表崩溃"的兜底。
    """
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db import session as session_module  # noqa: F401  触发 model 注册
    from app.db.base import Base

    async def _run() -> None:
        engine = create_async_engine(settings.database_url, future=True)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info(
                "ingest_schema_ensured",
                extra={"url": settings.database_url.split("://", 1)[0]},
            )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def main(argv: list[str] | None = None) -> int:
    """调度器入口：``python -m app.ingest.scheduler [--once [WINDOW]] [--trade-date D]``。"""
    parser = argparse.ArgumentParser(
        prog="app.ingest.scheduler",
        description=(
            "gold-desire 采集调度器（常驻或单跑；窗口 auction/intraday/tailpan/postmarket）"
        ),
    )
    parser.add_argument(
        "--once",
        nargs="?",
        const="",
        default=None,
        metavar="WINDOW",
        help="单跑一次；可附窗口名（缺省按当前时间判定到期任务）",
    )
    parser.add_argument("--trade-date", default=None, metavar="YYYY-MM-DD", help="目标交易日")
    parser.add_argument("--tick", type=int, default=None, metavar="SECONDS", help="tick 间隔秒数")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging()
    _ensure_schema(settings)
    scheduler = IngestScheduler(settings, tick_seconds=args.tick)

    if args.once is None:
        asyncio.run(scheduler.run_forever())
        return 0

    window = args.once or None
    trade_date = date.fromisoformat(args.trade_date) if args.trade_date else None
    results = asyncio.run(scheduler.run_once(window=window, trade_date=trade_date))
    for result in results:
        print(
            f"[{result.task}] {result.status} rows={result.rows} "
            f"source={result.source} error={result.error}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
