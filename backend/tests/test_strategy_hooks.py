"""策略阶段钩子测试：盘后自动执行 POOL/INTRADAY、幂等、失败重试与 WS 推送。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, UTC
from typing import Any, ClassVar

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.datasources.base import BaseProvider, SourceKind
from app.ingest.scheduler import IngestScheduler
from app.ingest.strategy_hooks import run_strategy_phases, strategy_state_name
from app.repositories import Repositories
from app.strategies.protocol import Phase
from tests.conftest import TRADE_DATE, seed_market


class FixedCalendarProvider(BaseProvider):
    """固定日历假源：只声明 trading_calendar，返回覆盖测试日的开市日。

    消除对全局能力主备顺序 / 网络可用性的依赖（fake 默认日历仅含 2026-09-18，
    会把 2026-06-03 判为非交易日导致调度器跳过）。
    """

    source_id: ClassVar[str] = "fake"
    label: ClassVar[str] = "固定日历源"
    kind: ClassVar[SourceKind] = SourceKind.FAKE
    capabilities: ClassVar[tuple[str, ...]] = ("trading_calendar",)
    rate_limit_per_min: ClassVar[int] = 600

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        return {
            "days": [
                {"cal_date": "20260602", "open_flag": 1},
                {"cal_date": "20260603", "open_flag": 1},
                {"cal_date": "20260604", "open_flag": 1},
            ]
        }


@pytest.fixture
async def seeded(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """种入行情 + 策略/因子定义与参数版本（seed_market 全集）。"""
    await seed_market(session_factory)
    yield session_factory


# ============================================================ 1. 执行与幂等


async def test_runs_both_phases_and_is_idempotent(seeded) -> None:
    """首次运行两阶段并落 succeeded 状态；再次运行全部跳过。"""
    async with seeded() as session:
        repos = Repositories.build(session)
        summaries = await run_strategy_phases(repos, get_settings(), TRADE_DATE)
        await session.commit()

    assert [summary.phase for summary in summaries] == [Phase.POOL, Phase.INTRADAY]
    assert all(summary.failure_count == 0 for summary in summaries)

    async with seeded() as session:
        repos = Repositories.build(session)
        statuses = {
            phase: await repos.pool_snapshot.get(TRADE_DATE, strategy_state_name(phase))
            for phase in (Phase.POOL, Phase.INTRADAY)
        }
        again = await run_strategy_phases(repos, get_settings(), TRADE_DATE)
    assert all(row is not None and row.payload["status"] == "succeeded" for row in statuses.values())
    assert again == []


# ============================================================ 2. 失败重试


async def test_failed_phase_is_retried(seeded, monkeypatch: pytest.MonkeyPatch) -> None:
    """阶段失败记 failed、不标记成功 → 下一轮重试。"""
    from app.ingest import strategy_hooks

    calls = 0

    async def _boom(phase, factory, repos):
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(strategy_hooks, "run_phase", _boom)
    async with seeded() as session:
        repos = Repositories.build(session)
        await run_strategy_phases(repos, get_settings(), TRADE_DATE)
        await session.commit()
        row = await repos.pool_snapshot.get(TRADE_DATE, strategy_state_name(Phase.POOL))
        assert row is not None and row.payload["status"] == "failed"

    monkeypatch.undo()
    async with seeded() as session:
        repos = Repositories.build(session)
        summaries = await run_strategy_phases(repos, get_settings(), TRADE_DATE)
    assert calls == 2
    assert all(summary.failure_count == 0 for summary in summaries)


# ============================================================ 3. WS 推送


async def test_pool_event_broadcast(seeded) -> None:
    """POOL 完成后经 ws_bus 推送 pool 事件（内存缓存回退本地广播器）。"""
    from app.core import ws_bus

    events: list[tuple[str, object]] = []

    async def _capture(channel: str, payload: object) -> int:
        events.append((channel, payload))
        return 0

    ws_bus.set_local_broadcaster(_capture)
    try:
        async with seeded() as session:
            repos = Repositories.build(session)
            await run_strategy_phases(repos, get_settings(), TRADE_DATE)
    finally:
        ws_bus.set_local_broadcaster(None)

    pool_events = [payload for channel, payload in events if channel == "pool"]
    assert pool_events, "应至少推送一条 pool 事件"
    body = pool_events[-1]
    assert body["source"] == "strategy:pool"
    assert body["trade_date"] == TRADE_DATE.isoformat()
    assert isinstance(body["count"], int)
    # 无建议产出时不推送 advice 事件（有产出才推）
    if any(channel == "advice" for channel, _ in events):
        advice_body = next(payload for channel, payload in events if channel == "advice")
        assert advice_body["count"] == len(advice_body["advices"])


# ============================================================ 4. 调度器接线


async def test_scheduler_postmarket_runs_strategies(
    seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """postmarket 窗口触发策略阶段；auction 窗口不触发。"""
    from app.ingest import scheduler as scheduler_module
    from app.ingest.pipeline import IngestResult

    async def _fake_run_task(defn, trade_date, **kwargs):
        return IngestResult(
            task=defn.name,
            capability=defn.capability,
            trade_date=trade_date,
            status="succeeded",
            rows=0,
            source="fake",
            job_id=f"job-{defn.name}",
            error=None,
            attempts=1,
            duration_ms=1,
        )

    monkeypatch.setattr(scheduler_module, "run_task", _fake_run_task)
    settings = get_settings()
    evening = datetime(2026, 6, 3, 17, 30, tzinfo=UTC)
    sched = IngestScheduler(
        settings,
        session_factory=seeded,
        provider_override=FixedCalendarProvider(),
        now_fn=lambda: evening,
        tick_seconds=1,
    )

    await sched.run_once(window="postmarket", trade_date=TRADE_DATE, now=evening)
    async with seeded() as session:
        repos = Repositories.build(session)
        for phase in (Phase.POOL, Phase.INTRADAY):
            row = await repos.pool_snapshot.get(TRADE_DATE, strategy_state_name(phase))
            assert row is not None and row.payload["status"] == "succeeded", phase

    # 另一交易日走 auction 窗口：不触发策略阶段
    other = date(2026, 6, 4)
    morning = datetime(2026, 6, 4, 9, 30, tzinfo=UTC)
    await sched.run_once(window="auction", trade_date=other, now=morning)
    async with seeded() as session:
        repos = Repositories.build(session)
        assert await repos.pool_snapshot.get(other, strategy_state_name(Phase.POOL)) is None
        assert await repos.pool_snapshot.get(other, strategy_state_name(Phase.INTRADAY)) is None
