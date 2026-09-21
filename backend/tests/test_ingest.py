"""采集入库管道测试（SQLite / aiosqlite，零网络、零 PostgreSQL/Redis）。

覆盖：行级幂等重跑、raw 留档、失败记录不吞错且任务隔离、回放守卫（不触达 provider）、
调度器 DB 幂等状态与失败重试、交易日历失败兜底、窗口过滤。
所有上游调用均由注入的假 provider 承载，SHALL NOT 触达真实数据源。
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import pytest
from app.core.config import get_settings
from app.core.errors import SnapshotMissingError, UpstreamError
from app.datasources.base import BaseProvider, SourceKind, reset_buckets
from app.datasources.contracts import CAPABILITY_CONTRACTS
from app.datasources.providers.fake import default_payloads
from app.datasources.registry import register_provider, reset_capability_order, set_capability_order
from app.datasources.resolve import reset_replay_source, resolve
from app.db.base import Base
from app.ingest.pipeline import run_many, run_task
from app.ingest.replay import replay_scope
from app.ingest.scheduler import IngestScheduler, is_trading_day
from app.ingest.tasks import get_task
from app.models.derived import IngestJob
from app.models.market import LimitUpPool
from app.models.raw import RawResponse
from app.repositories import Repositories
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

SH = ZoneInfo("Asia/Shanghai")
TRADE_DATE = date(2026, 9, 18)


# ============================================================ 注入用假 provider


class RecordingProvider(BaseProvider):
    """记录调用、可定向失败的假源；``source_id='fake'`` 以复用既有映射。"""

    source_id: ClassVar[str] = "fake"
    label: ClassVar[str] = "记录用假源"
    kind: ClassVar[SourceKind] = SourceKind.FAKE
    capabilities: ClassVar[tuple[str, ...]] = tuple(CAPABILITY_CONTRACTS)
    rate_limit_per_min: ClassVar[int] = 600

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self._payloads = default_payloads()
        self.fail_for = set(fail_for or ())
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        """记录调用；命中 ``fail_for`` 则抛 ``UpstreamError``。"""
        self.calls.append((capability, args))
        if capability in self.fail_for:
            raise UpstreamError(f"模拟失败：{capability}", detail={"capability": capability})
        return copy.deepcopy(self._payloads[capability])


@register_provider
class SpyProvider(BaseProvider):
    """仅用于验证回放守卫不会触达 provider 的探针源。"""

    source_id: ClassVar[str] = "ingest_spy"
    label: ClassVar[str] = "回放探针源"
    kind: ClassVar[SourceKind] = SourceKind.FAKE
    capabilities: ClassVar[tuple[str, ...]] = ("daily_bars",)
    rate_limit_per_min: ClassVar[int] = 600

    calls: ClassVar[list[tuple[str, dict[str, Any]]]] = []

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        """记录调用并返回空载荷（回放路径不应走到这里）。"""
        SpyProvider.calls.append((capability, args))
        return {}


# ============================================================ 夹具


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    """每例前后重置限流桶、能力顺序、回放读取器与探针记录。"""
    reset_buckets()
    reset_capability_order()
    reset_replay_source()
    SpyProvider.calls.clear()
    yield
    reset_buckets()
    reset_capability_order()
    reset_replay_source()
    SpyProvider.calls.clear()


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """内存 SQLite 会话工厂；每例独立建库以保证隔离。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _count(session: AsyncSession, model: type[Any], **filters: Any) -> int:
    """按等值条件统计行数。"""
    stmt = select(func.count()).select_from(model)
    for key, value in filters.items():
        stmt = stmt.where(getattr(model, key) == value)
    return int(await session.scalar(stmt) or 0)


def _daily_row(code: str, trade_date: date) -> dict[str, Any]:
    """构造一条合法日线行字典。"""
    return {
        "code": code,
        "trade_date": trade_date,
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "pre_close": 10.0,
        "volume_shares": 1_000_000,
        "amount_yuan": 1_000_000.0,
        "source": "test",
    }


async def _seed_main_board_ladder(
    factory: async_sessionmaker[AsyncSession],
    code: str = "600519.SH",
    name: str = "贵州茅台",
) -> None:
    """预置一条主板连板天梯记录。

    ``daily_bars`` 的标的由「近期涨停池 + 连板天梯」推导（主板 + 非 ST），
    故需先有这样一个标的该任务才确有票可采。
    """
    async with factory() as session:
        repos = Repositories.build(session)
        await repos.ladder.upsert_many(
            [
                {
                    "trade_date": TRADE_DATE,
                    "code": code,
                    "name": name,
                    "continue_days": 2,
                    "source": "test",
                }
            ]
        )
        await session.commit()


# ============================================================ 1. 幂等


async def test_rerun_same_task_does_not_duplicate_rows(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一任务同一交易日跑两次：行数不变，IngestJob 留两次运行痕迹。"""
    provider = RecordingProvider()
    async with factory() as session:
        repos = Repositories.build(session)
        first = await run_task(
            get_task("limit_up_pool"),
            TRADE_DATE,
            repos=repos,
            settings=get_settings(),
            provider_override=provider,
        )
        await session.commit()
    async with factory() as session:
        repos = Repositories.build(session)
        second = await run_task(
            get_task("limit_up_pool"),
            TRADE_DATE,
            repos=repos,
            settings=get_settings(),
            provider_override=provider,
        )
        await session.commit()

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    async with factory() as session:
        assert await _count(session, LimitUpPool, trade_date=TRADE_DATE) == 1
        assert await _count(session, IngestJob, capability="limit_up_pool") == 2


# ============================================================ 2. raw 留档


async def test_raw_response_is_archived(factory: async_sessionmaker[AsyncSession]) -> None:
    """一次运行写入一条 RawResponse（source/capability/sha256）。"""
    provider = RecordingProvider()
    async with factory() as session:
        repos = Repositories.build(session)
        await run_task(
            get_task("limit_up_pool"),
            TRADE_DATE,
            repos=repos,
            settings=get_settings(),
            provider_override=provider,
        )
        await session.commit()

    async with factory() as session:
        raws = list((await session.execute(select(RawResponse))).scalars().all())

    assert len(raws) == 1
    assert raws[0].source == "fake"
    assert raws[0].capability == "limit_up_pool"
    assert raws[0].trade_date == TRADE_DATE
    assert len(raws[0].sha256) == 64
    assert "items" in raws[0].payload["data"]


# ============================================================ 3. 失败不吞错 + 隔离


async def test_failure_recorded_not_swallowed_and_isolated(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """上游失败：任务行 failed + error；run_many 中其余任务仍成功。"""
    provider = RecordingProvider(fail_for={"daily_bars"})
    await _seed_main_board_ladder(factory)
    async with factory() as session:
        repos = Repositories.build(session)
        results = await run_many(
            [get_task("limit_up_pool"), get_task("daily_bars")],
            TRADE_DATE,
            repos=repos,
            settings=get_settings(),
            provider_override=provider,
        )
        await session.commit()

    by_task = {result.task: result for result in results}
    assert by_task["limit_up_pool"].status == "succeeded"
    assert by_task["daily_bars"].status == "failed"
    assert by_task["daily_bars"].error
    assert "daily_bars" in (by_task["daily_bars"].error or "")

    async with factory() as session:
        job = (
            (await session.execute(select(IngestJob).where(IngestJob.capability == "daily_bars")))
            .scalars()
            .one()
        )
        assert job.status == "failed"
        assert job.error
        assert job.attempts == 1
        assert await _count(session, LimitUpPool, trade_date=TRADE_DATE) == 1


# ============================================================ 4. 回放守卫


async def test_replay_scope_blocks_upstream_and_requires_snapshot(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """回放作用域内：无快照抛 SnapshotMissingError，有快照返回库数据；全程不触达 provider。"""
    set_capability_order("daily_bars", ["ingest_spy"])
    async with factory() as session:
        repos = Repositories.build(session)
        with replay_scope(TRADE_DATE, repos), pytest.raises(SnapshotMissingError) as excinfo:
            await resolve("daily_bars", replay_date=TRADE_DATE)
        assert "daily_bars" in str(excinfo.value)
        assert "禁止" in str(excinfo.value)
        assert SpyProvider.calls == []

        await repos.daily_bars.upsert_many([_daily_row("600519.SH", TRADE_DATE)])
        await session.commit()
        with replay_scope(TRADE_DATE, repos):
            rows = await resolve("daily_bars", replay_date=TRADE_DATE)
        assert len(rows) == 1
        assert rows[0].code == "600519.SH"
        assert SpyProvider.calls == []


# ============================================================ 5. 调度器状态


async def test_scheduler_state_prevents_rerun_and_retries_failures(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """已完成任务不重跑；失败任务不标记完成，窗口内重试。"""
    provider = RecordingProvider()
    settings = get_settings()
    morning = datetime(2026, 9, 18, 9, 30, tzinfo=SH)
    sched = IngestScheduler(
        settings,
        session_factory=factory,
        provider_override=provider,
        now_fn=lambda: morning,
    )
    first = await sched.run_once(window="auction")
    assert [result.task for result in first] == ["limit_up_pool"]
    assert await sched.run_once(window="auction") == []
    async with factory() as session:
        assert await _count(session, IngestJob, capability="limit_up_pool") == 1

    provider.fail_for = {"daily_bars"}
    await _seed_main_board_ladder(factory)
    evening = datetime(2026, 9, 18, 17, 30, tzinfo=SH)
    sched_pm = IngestScheduler(
        settings,
        session_factory=factory,
        provider_override=provider,
        now_fn=lambda: evening,
    )
    pm_first = await sched_pm.run_once(window="postmarket")
    assert {result.task for result in pm_first if result.status == "failed"} == {"daily_bars"}
    pm_second = await sched_pm.run_once(window="postmarket")
    assert [result.task for result in pm_second] == ["daily_bars"]
    async with factory() as session:
        assert await _count(session, IngestJob, capability="daily_bars") == 2


# ============================================================ 6. 日历兜底


async def test_calendar_failure_falls_back_to_weekdays(
    factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    """日历上游失败：按周一至五判定并记警告，调度器不崩溃。"""
    provider = RecordingProvider(fail_for={"trading_calendar"})
    settings = get_settings()
    async with factory() as session:
        repos = Repositories.build(session)
        with caplog.at_level("WARNING"):
            friday = await is_trading_day(
                repos, settings, date(2026, 9, 18), provider_override=provider
            )
            saturday = await is_trading_day(
                repos, settings, date(2026, 9, 19), provider_override=provider
            )
        await session.commit()

    assert friday is True
    assert saturday is False
    assert any("交易日历" in record.getMessage() for record in caplog.records)

    sched = IngestScheduler(
        settings,
        session_factory=factory,
        provider_override=provider,
        now_fn=lambda: datetime(2026, 9, 18, 9, 30, tzinfo=SH),
    )
    results = await sched.run_once(window="auction")
    assert [result.task for result in results] == ["limit_up_pool"]


# ============================================================ 7. 窗口过滤


async def test_run_once_window_filter(factory: async_sessionmaker[AsyncSession]) -> None:
    """``run_once(window=...)`` 只执行该窗口到期的任务。"""
    provider = RecordingProvider()
    settings = get_settings()
    sched = IngestScheduler(
        settings,
        session_factory=factory,
        provider_override=provider,
        now_fn=lambda: datetime(2026, 9, 18, 14, 50, tzinfo=SH),
    )
    results = await sched.run_once(window="tailpan")
    assert {result.task for result in results} == {"theme_rank", "theme_stocks"}

    async with factory() as session:
        capabilities = set((await session.execute(select(IngestJob.capability))).scalars().all())
    assert capabilities == {"theme_rank", "theme_stocks"}
    assert "daily_bars" not in capabilities


# ============================================================ 8. 周期任务重跑（Bug #1 回归）


async def test_interval_task_reruns_after_success_within_window(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """回归 Bug #1（docs/bugs-2026-09-21.md）：interval>0 任务成功后按间隔重跑。

    历史缺陷：``run_once`` 先判 ``_state_status == succeeded`` 再判 interval，
    newsflash 09:26 成功后被 succeeded 状态永久跳过——整个 intraday 窗口
    只跑 1 次，interval_seconds=60 形同虚设。
    """
    provider = RecordingProvider()
    settings = get_settings()
    clock = {"now": datetime(2026, 9, 18, 9, 26, 2, tzinfo=SH)}
    sched = IngestScheduler(
        settings, session_factory=factory, provider_override=provider, now_fn=lambda: clock["now"]
    )

    first = await sched.run_once(window="intraday")
    assert [result.task for result in first] == ["newsflash"]
    assert first[0].status == "succeeded"

    # +30s：未到 60s 间隔 → 不跑
    clock["now"] = clock["now"] + timedelta(seconds=30)
    assert await sched.run_once(window="intraday") == []

    # +61s：到期 → 重跑（即便上次已 succeeded）
    clock["now"] = clock["now"] + timedelta(seconds=31)
    again = await sched.run_once(window="intraday")
    assert [result.task for result in again] == ["newsflash"]
    assert again[0].status == "succeeded"

    # 一次性任务（limit_up_pool）语义不变：成功后同窗口不重跑
    morning = datetime(2026, 9, 18, 9, 30, tzinfo=SH)
    clock["now"] = morning
    oneshot = IngestScheduler(
        settings, session_factory=factory, provider_override=provider, now_fn=lambda: clock["now"]
    )
    assert [r.task for r in await oneshot.run_once(window="auction")] == ["limit_up_pool"]
    assert await oneshot.run_once(window="auction") == []


async def test_interval_task_survives_scheduler_restart(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """周期任务不受「当日已完成」状态约束：重启（新调度器实例）后下一 tick 即重跑。"""
    provider = RecordingProvider()
    settings = get_settings()
    moment = datetime(2026, 9, 18, 9, 26, 2, tzinfo=SH)
    first_sched = IngestScheduler(
        settings, session_factory=factory, provider_override=provider, now_fn=lambda: moment
    )
    await first_sched.run_once(window="intraday")

    # 模拟 worker 重启：全新实例（_last_attempt 为空），同一时刻重启
    restarted = IngestScheduler(
        settings, session_factory=factory, provider_override=provider, now_fn=lambda: moment
    )
    results = await restarted.run_once(window="intraday")
    assert [result.task for result in results] == ["newsflash"]


# ============================================================ 9. 快讯落库（Bug #2 回归）


async def test_newsflash_rows_land_in_db_and_queryable(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """回归 Bug #2（docs/bugs-2026-09-21.md）：newsflash 任务 rows 与表内行数一致。

    ``run_task`` 的 news_flash 写入与 ``ingest_jobs`` 留痕在同一事务，随
    ``run_once`` 的 ``session.commit()`` 一并持久化；任务报 succeeded rows=N
    时，``news_flash`` 表必然可查到这 N 行（重跑覆盖写不重复计行）。
    """
    provider = RecordingProvider()
    settings = get_settings()
    sched = IngestScheduler(
        settings,
        session_factory=factory,
        provider_override=provider,
        now_fn=lambda: datetime(2026, 9, 18, 9, 26, 2, tzinfo=SH),
    )
    results = await sched.run_once(window="intraday")
    newsflash = next(result for result in results if result.task == "newsflash")
    assert newsflash.status == "succeeded"

    async with factory() as session:
        repos = Repositories.build(session)
        rows = await repos.news_flash.list_recent(limit=100)
    assert len(rows) == newsflash.rows
    assert rows == sorted(rows, key=lambda row: row.ts, reverse=True)
    # 注：PG 侧 ts 列为 timestamptz、写入值为上海时区 aware datetime（绝对时间正确）；
    # SQLite 测试库回读丢失 tzinfo，故此处不断言 tzinfo。
