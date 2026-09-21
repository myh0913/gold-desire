"""采集完成事件测试：WS 薄事件广播 + 读缓存前缀失效（含调度器接线）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any, ClassVar

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from app.core import ws_bus
from app.core.cache import get_cache
from app.core.config import get_settings
from app.db.base import Base
from app.datasources.base import BaseProvider, SourceKind
from app.ingest.events import notify_ingest_completed
from app.ingest.pipeline import IngestResult

SH_DATE = date(2026, 6, 3)


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """内存 SQLite 会话工厂（独立建库）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _CalendarProvider(BaseProvider):
    """固定日历假源（source_id='fake' 复用既有映射），消除网络依赖。"""

    source_id: ClassVar[str] = "fake"
    label: ClassVar[str] = "事件测试日历源"
    kind: ClassVar[SourceKind] = SourceKind.FAKE
    capabilities: ClassVar[tuple[str, ...]] = ("trading_calendar",)
    rate_limit_per_min: ClassVar[int] = 600

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        return {"days": [{"cal_date": "20260918", "open_flag": 1}]}


def _result(capability: str, rows: int = 5, status: str = "succeeded") -> IngestResult:
    return IngestResult(
        task=f"task:{capability}",
        capability=capability,
        trade_date=SH_DATE,
        status=status,
        rows=rows,
        source="fake",
        job_id=f"job-{capability}",
        error=None,
        attempts=1,
        duration_ms=1,
    )


# ============================================================ 事件广播


async def test_completed_broadcasts_thin_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """limit_up_pool 成功 → pool 频道薄事件（无全量 payload）。"""
    published: list[tuple[str, object]] = []

    async def _publish(channel: str, payload: object) -> None:
        published.append((channel, payload))

    monkeypatch.setattr(ws_bus, "publish_event", _publish)
    await notify_ingest_completed(_result("limit_up_pool"), SH_DATE)

    assert [channel for channel, _ in published] == ["pool"]
    body = published[0][1]
    assert body["source"] == "ingest:limit_up_pool"
    assert body["trade_date"] == "2026-06-03"
    assert body["rows"] == 5


async def test_unknown_or_failed_capability_no_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未登记能力（trading_calendar）不发不失效；失败结果由调用方过滤（此处直呼也安全）。"""
    published: list[tuple[str, object]] = []

    async def _publish(channel: str, payload: object) -> None:
        published.append((channel, payload))

    monkeypatch.setattr(ws_bus, "publish_event", _publish)
    await notify_ingest_completed(_result("trading_calendar"), SH_DATE)
    assert published == []


# ============================================================ 缓存失效


async def test_completed_invalidates_cache_namespaces() -> None:
    """market_sentiment 成功 → sentiment_live / sentiment_history 前缀被清除。"""
    cache = get_cache()
    await cache.set("sentiment_live:date=2026-06-03", {"x": 1}, ttl=60)
    await cache.set("sentiment_history:days=20", {"y": 2}, ttl=60)
    await cache.set("pool:date=2026-06-03", {"z": 3}, ttl=60)

    await notify_ingest_completed(_result("market_sentiment"), SH_DATE)

    assert await cache.get("sentiment_live:date=2026-06-03") is None
    assert await cache.get("sentiment_history:days=20") is None
    # 无关命名空间不受影响
    assert await cache.get("pool:date=2026-06-03") is not None


async def test_daily_bars_invalidates_without_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """daily_bars 只失效缓存（日线/分时），不广播任何 WS 频道。"""
    published: list[tuple[str, object]] = []

    async def _publish(channel: str, payload: object) -> None:
        published.append((channel, payload))

    monkeypatch.setattr(ws_bus, "publish_event", _publish)
    cache = get_cache()
    await cache.set("daily_bars:code=600001", {"a": 1}, ttl=60)

    await notify_ingest_completed(_result("daily_bars"), SH_DATE)

    assert published == []
    assert await cache.get("daily_bars:code=600001") is None


# ============================================================ 调度器接线


async def test_scheduler_notifies_on_success(factory, monkeypatch: pytest.MonkeyPatch) -> None:
    """调度器成功的采集任务触发事件；失败任务不触发。"""
    from app.ingest import scheduler as scheduler_module
    from app.ingest.scheduler import IngestScheduler

    outcomes: list[tuple[str, str]] = []  # (capability, status)

    async def _fake_run_task(defn, trade_date, **kwargs):
        status = "failed" if defn.capability == "daily_bars" else "succeeded"
        outcomes.append((defn.capability, status))
        return IngestResult(
            task=defn.name,
            capability=defn.capability,
            trade_date=trade_date,
            status=status,
            rows=1,
            source="fake",
            job_id=f"job-{defn.name}",
            error=None if status == "succeeded" else "boom",
            attempts=1,
            duration_ms=1,
        )

    notified: list[str] = []

    async def _notify(result, trade_date):
        notified.append(str(result.capability))

    monkeypatch.setattr(scheduler_module, "run_task", _fake_run_task)
    monkeypatch.setattr("app.ingest.events.notify_ingest_completed", _notify)

    evening = datetime(2026, 9, 18, 17, 30, tzinfo=UTC)
    sched = IngestScheduler(
        get_settings(),
        session_factory=factory,
        provider_override=_CalendarProvider(),
        now_fn=lambda: evening,
        tick_seconds=1,
    )
    await sched.run_once(window="postmarket", trade_date=date(2026, 9, 18), now=evening)

    executed = {cap for cap, status in outcomes}
    # 只有 succeeded 的能力进入通知（daily_bars 失败被过滤）
    assert "daily_bars" not in notified
    assert "market_sentiment" in notified
    assert notified and set(notified) <= executed
