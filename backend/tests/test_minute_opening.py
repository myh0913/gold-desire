"""minute_bars / opening_match 能力链 + Phase.OPENING 盘中提示测试。

覆盖：
- fake 源 minute_bars 任务端到端落库（契约映射 → minute_bars 表）；
- opening_match 任务端到端（撮合价 → pool_snapshot）；
- 竞价窗口调度：opening_match 成功后自动跑 Phase.OPENING，S2 以当日开盘价
  命中并落 advice_reports（buy_day=今日）——「次日开盘买点」盘中提示链路；
- build_opening_samples 的 S2 视角与排除条件；
- sanbanzu / suspect / 交易日邻接纯逻辑。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import pytest
from app.core.config import get_settings
from app.datasources.base import BaseProvider, SourceKind
from app.datasources.providers.fake import FakeProvider
from app.engine.dragon_samples import (
    _dates_adjacent,
    _has_suspect_day,
    _is_sanbanzu_wave,
    build_opening_samples,
    minute_time_label,
)
from app.ingest.scheduler import IngestScheduler
from app.ingest.tasks import OPENING_MATCH_POOL_NAME
from app.repositories import Repositories
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import STOCK_CODE, seed_market

SH = ZoneInfo("Asia/Shanghai")
TODAY = date(2026, 6, 4)  # 06-03 为首阴 D（conftest 日线结构），06-04 为 D+1 开盘日


class _OpeningProvider(BaseProvider):
    """fake 兜底 + 按票注入撮合价 + 六月日历（source_id='fake' 复用既有映射）。"""

    source_id: ClassVar[str] = "fake"
    label: ClassVar[str] = "撮合注入假源"
    kind: ClassVar[SourceKind] = SourceKind.FAKE
    capabilities: ClassVar[tuple[str, ...]] = ("minute_bars", "opening_match")
    rate_limit_per_min: ClassVar[int] = 600

    def __init__(self, opening: dict[str, float]) -> None:
        self._opening = opening
        self._fake = FakeProvider()

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        if capability == "trading_calendar":
            # 06-01 ~ 06-06 全部视为开市（覆盖测试窗口）。
            return {
                "days": [
                    {"cal_date": f"2026060{d}", "open_flag": 1} for d in range(1, 7)
                ]
            }
        if capability == "opening_match":
            price = self._opening.get(str(args.get("thscode", "")))
            if price is None:
                return {"matches": []}
            return {"matches": [{"price": price, "volume": 100.0, "time_label": "09:25"}]}
        return await self._fake.fetch(capability, **args)


async def _seed_dive_minutes(
    session_factory: async_sessionmaker[AsyncSession], code: str, day: date
) -> None:
    """种入 D 日「尾盘跳水」形态的 240 点分时（低点在尾盘、收盘贴近低点）。"""
    async with session_factory() as session:
        repos = Repositories.build(session)
        pre = 17.666  # D 日（06-03）昨收
        rows = []
        for i in range(240):
            if i < 210:
                price = pre * 0.99
            else:
                price = pre * 0.99 - (pre * 0.07) * (i - 210) / 29.0
            rows.append(
                {
                    "code": code,
                    "trade_date": day,
                    "minute_index": i,
                    "time_label": minute_time_label(i),
                    "price": price,
                    "volume_lots": 100,
                    "amount_yuan": None,
                    "source": "fake",
                }
            )
        await repos.minute_bars.upsert_many(rows)
        await session.commit()


async def _seed_calendar(
    session_factory: async_sessionmaker[AsyncSession], dates: list[date]
) -> None:
    """种入交易日历缓存（键= TODAY，与调度器生产写入口径一致）。"""
    async with session_factory() as session:
        repos = Repositories.build(session)
        await repos.pool_snapshot.upsert_many(
            [
                {
                    "trade_date": TODAY,
                    "pool_name": "trading_calendar",
                    "payload": {"dates": [d.isoformat() for d in dates]},
                    "source": "fake",
                }
            ]
        )
        await session.commit()


@pytest.fixture
async def seeded(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """行情全集 + D 日尾盘跳水分时 + 06-01~06-06 交易日历。"""
    await seed_market(session_factory)
    await _seed_dive_minutes(session_factory, STOCK_CODE, date(2026, 6, 3))
    base = date(2026, 6, 1)
    await _seed_calendar(session_factory, [base + timedelta(days=i) for i in range(6)])
    yield session_factory


# ============================================================ 1. minute_bars 任务


async def test_minute_bars_task_lands_rows(seeded) -> None:
    """intraday_day 窗口跑 minute_bars 任务：契约映射 → minute_bars 表。"""
    settings = get_settings()
    sched = IngestScheduler(
        settings,
        session_factory=seeded,
        provider_override=_OpeningProvider({}),
        now_fn=lambda: datetime(2026, 6, 4, 9, 31, tzinfo=SH),
        tick_seconds=1,
    )
    results = await sched.run_once(window="intraday_day", trade_date=TODAY)
    tasks = {result.task: result for result in results}
    assert tasks["minute_bars"].status == "succeeded"

    async with seeded() as session:
        repos = Repositories.build(session)
        rows = await repos.minute_bars.get_day("600001", TODAY)
    assert len(rows) == 3  # fake payload 3 个分钟点（06-04 无 fixture 预置分时）
    assert rows[0].time_label == "09:31"
    assert rows[0].amount_yuan is None  # eltdx 无成交额 → NULL（迁移 0003）


# ============================================================ 2. 竞价窗口 → OPENING 提示


async def test_auction_window_produces_opening_advice(seeded) -> None:
    """opening_match 成功后自动跑 OPENING：S2 以当日撮合价命中并落建议。"""
    settings = get_settings()
    morning = datetime(2026, 6, 4, 9, 26, tzinfo=SH)
    sched = IngestScheduler(
        settings,
        session_factory=seeded,
        provider_override=_OpeningProvider({STOCK_CODE: 16.2}),
        now_fn=lambda: morning,
        tick_seconds=1,
    )
    results = await sched.run_once(window="auction", trade_date=TODAY)
    assert {result.task for result in results} >= {"opening_match"}

    async with seeded() as session:
        repos = Repositories.build(session)
        # 撮合价已落 pool_snapshot
        snap = await repos.pool_snapshot.get(TODAY, OPENING_MATCH_POOL_NAME)
        assert snap is not None and snap.payload[STOCK_CODE]["price"] == pytest.approx(16.2)
        # OPENING 阶段状态成功
        state = await repos.pool_snapshot.get(TODAY, "strategy_state:opening")
        assert state is not None and state.payload["status"] == "succeeded"
        # S2 建议落库：报告按运行日（今日）落库；buy_day=今日、买价=撮合价、仓位=基础仓
        reports = await repos.advice_reports.get_by_date(TODAY, kind="advice")
        s2 = [
            row
            for row in reports
            if row.payload.get("path_id") == "S2"
            and row.payload.get("buy_day") == TODAY.isoformat()
        ]
        assert s2, "应产出 S2 开盘建议"
        assert s2[0].payload["buy_price"] == pytest.approx(16.2)
        # 仓位 = 生效配置的 base_position（conftest 种子激活 v2 = 0.25）
        assert s2[0].payload["position"] == pytest.approx(0.25)


# ============================================================ 3. build_opening_samples


async def test_build_opening_samples_views(seeded) -> None:
    """S2（D=昨日，t.open 注入）视角可构造；无撮合价的票不产样本。"""
    async with seeded() as session:
        repos = Repositories.build(session)
        samples = await build_opening_samples(repos, TODAY, {STOCK_CODE: 16.2})
    s2 = [sample for sample in samples if sample.D == date(2026, 6, 3)]
    assert s2, "应构造出 D=06-03 的 S2 开盘样本"
    assert s2[0].t.open == pytest.approx(16.2)
    assert s2[0].t.open_pct == pytest.approx(16.2 / 16.9 - 1)
    assert s2[0].shape_label == "尾盘跳水"

    async with seeded() as session:
        repos = Repositories.build(session)
        assert await build_opening_samples(repos, TODAY, {"600003": 10.0}) == []


async def test_build_opening_samples_requires_calendar(seeded) -> None:
    """无交易日历时返回空（开盘判定依赖日历推上一/上上交易日）。"""
    async with seeded() as session:
        repos = Repositories.build(session)
        # 覆盖 anchor 键（TODAY）为空日历 → _load_trading_dates 返回 None
        await repos.pool_snapshot.upsert_many(
            [
                {
                    "trade_date": TODAY,
                    "pool_name": "trading_calendar",
                    "payload": {"dates": []},
                    "source": "fake",
                }
            ]
        )
        await session.commit()
        assert await build_opening_samples(repos, TODAY, {STOCK_CODE: 16.2}) == []


# ============================================================ 4. 纯逻辑


def _bar(day: date, pre: float, o: float, h: float, low: float, close: float, vol: float):
    return SimpleNamespace(
        trade_date=day, open=o, high=h, low=low, close=close, pre_close=pre, volume_shares=vol
    )


def test_suspect_rejects_abnormal_day() -> None:
    """单日涨跌幅越界 ±10.5% → 整票拒收。"""
    days = [
        _bar(date(2026, 1, 1), 10.0, 10.0, 10.5, 9.9, 10.2, 100),
        _bar(date(2026, 1, 2), 10.2, 10.2, 10.6, 10.1, 10.4, 100),
        _bar(date(2026, 1, 3), 10.4, 10.4, 10.8, 10.3, 10.6, 100),
    ]
    assert _has_suspect_day(days) is False
    days.append(_bar(date(2026, 1, 4), 10.6, 10.6, 12.72, 10.6, 12.72, 100))  # +20% 越界
    assert _has_suspect_day(days) is True


def test_sanbanzu_wave_detected() -> None:
    """恰 3 板 + 后两板一字 + 一字极度缩量 → 三板组。"""
    pre = _bar(date(2026, 1, 1), 9.0, 9.1, 9.6, 9.0, 9.5, 1000)
    d1 = _bar(date(2026, 1, 2), 9.5, 9.6, 10.45, 9.55, 10.45, 800)  # 首板
    d2 = _bar(date(2026, 1, 3), 10.45, 11.5, 11.5, 11.5, 11.5, 50)  # 一字
    d3 = _bar(date(2026, 1, 4), 11.5, 12.65, 12.65, 12.65, 12.65, 40)  # 一字
    assert _is_sanbanzu_wave([d1, d2, d3], pre) is True
    # 后两板非一字 → 不是三板组
    d2_open = _bar(date(2026, 1, 3), 10.45, 10.5, 11.5, 10.4, 11.5, 50)
    assert _is_sanbanzu_wave([d1, d2_open, d3], pre) is False


def test_missing_trading_day_breaks_adjacency() -> None:
    """缺采的交易日（日历标记为开市）打断「紧邻」。"""
    calendar = {date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)}
    assert _dates_adjacent(date(2026, 1, 6), date(2026, 1, 7), None) is True  # 无日历回退
    assert _dates_adjacent(date(2026, 1, 6), date(2026, 1, 7), calendar) is True
    assert _dates_adjacent(date(2026, 1, 6), date(2026, 1, 8), calendar) is False  # 夹 01-07
