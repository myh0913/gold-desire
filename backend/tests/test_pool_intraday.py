"""涨停池盘中轮询改造测试（SQLite / aiosqlite，零网络、零 PostgreSQL/Redis）。

覆盖 2026-09-21 的改造（对齐 quant 的「盘中实时刷新 + 只留最新快照」口径）：

1. ``trading_hours`` 窗口 09:25-15:05，且**跳过午休** 11:30-13:00；
2. ``limit_up_pool`` 任务改为每 10 分钟一轮（``interval_seconds=600``）、
   走**整批替换**写入器（``target="limit_up_pool_replace"``）；
3. 替换语义：先涨停后炸板的票不残留、其他池型不受影响、空结果不清库；
4. 全交易时刻表：上午 13 轮、午休不拉、收盘后仍有最后一刀；
5. 保留策略：历史只留最近 30 个交易日（不足时按自然日回退）。

所有上游调用均由注入的假 provider 承载，SHALL NOT 触达真实数据源。
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import pytest
from app.core.config import get_settings
from app.datasources.base import BaseProvider, SourceKind
from app.datasources.contracts import CAPABILITY_CONTRACTS
from app.datasources.providers.fake import default_payloads
from app.db.base import Base
from app.ingest.scheduler import IngestScheduler
from app.ingest.tasks import WRITERS, get_task
from app.ingest.windows import Window, in_window, load_windows, window_by_name
from app.models.market import LadderRow, LimitUpPool, MarketSentiment
from app.repositories import LimitUpPoolRepository
from app.repositories.retention import (
    DAILY_BAR_RETENTION_TRADING_DAYS,
    LADDER_RETENTION_DAYS,
    POOL_RETENTION_TRADING_DAYS,
    SENTIMENT_RETENTION_TRADING_DAYS,
    TRADING_DAY_FALLBACK_DAYS,
    run_retention,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

SH = ZoneInfo("Asia/Shanghai")

#: 基准交易日（周五），用于任务/仓储级断言。
TRADE_DATE = date(2026, 9, 18)


def _at(hour: int, minute: int, day: date = TRADE_DATE) -> datetime:
    """构造上海时区的某时刻。"""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SH)


# ============================================================ 假 provider


class PoolRecordingProvider(BaseProvider):
    """记录调用时刻的假源；``source_id='fake'`` 以复用既有声明式映射。"""

    source_id: ClassVar[str] = "fake"
    label: ClassVar[str] = "涨停池测试假源"
    kind: ClassVar[SourceKind] = SourceKind.FAKE
    capabilities: ClassVar[tuple[str, ...]] = tuple(CAPABILITY_CONTRACTS)
    rate_limit_per_min: ClassVar[int] = 600

    def __init__(self) -> None:
        self._payloads = default_payloads()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        """记录调用并返回假载荷。"""
        self.calls.append((capability, args))
        return copy.deepcopy(self._payloads[capability])


# ============================================================ 夹具


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 会话；每例独立建库以保证隔离。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
        yield opened
    await engine.dispose()


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """内存 SQLite 会话工厂（调度器用）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _pool_row(
    code: str,
    *,
    trade_date: date = TRADE_DATE,
    pool_type: str = "limit_up",
    continue_days: int = 1,
) -> dict[str, Any]:
    """构造一条合法涨停池行字典。"""
    return {
        "trade_date": trade_date,
        "code": code,
        "name": f"测试{code}",
        "continue_days": continue_days,
        "limit_up_time": "09:31",
        "seal_amount_yuan": None,
        "open_times": None,
        "turnover_rate": None,
        "amount_yuan": None,
        "market_cap_yuan": None,
        "pool_type": pool_type,
        "source": "test",
    }


# ============================================================ 1. 窗口与午休


def test_trading_hours_window_is_0925_to_1505() -> None:
    """新窗口边界：09:25 起、15:05 收口，两侧各差一分钟即失效。"""
    window = window_by_name(load_windows(), "trading_hours")
    assert (window.start, window.end) == ("09:25", "15:05")
    assert in_window(_at(9, 24), window) is False
    assert in_window(_at(9, 25), window) is True
    assert in_window(_at(15, 5), window) is True
    assert in_window(_at(15, 6), window) is False


@pytest.mark.parametrize(
    ("hm", "expected"),
    [
        ("11:29", True),
        ("11:30", False),
        ("12:00", False),
        ("12:59", False),
        ("13:00", False),
        ("13:01", True),
    ],
)
def test_trading_hours_window_skips_lunch_break(hm: str, expected: bool) -> None:
    """午休 11:30-13:00 整段跳过（含 11:30 与 13:00 两个端点）。"""
    window = window_by_name(load_windows(), "trading_hours")
    hour, minute = (int(part) for part in hm.split(":"))
    assert in_window(_at(hour, minute), window) is expected


def test_other_windows_keep_continuous_semantics() -> None:
    """``breaks`` 缺省为空：既有窗口语义不变（无午休跳过的窗口仍全天连续）。"""
    windows = {window.name: window for window in load_windows()}
    for name in ("auction", "intraday", "tailpan", "postmarket", "intraday_day"):
        assert windows[name].breaks == ()
    # 盘后窗口无断点，任意时刻都在窗口内
    assert in_window(_at(17, 30), windows["postmarket"]) is True
    # 显式构造带 breaks 的窗口（数据化，非硬编码）
    assert Window("x", "09:00", "10:00").breaks == ()


def test_env_override_preserves_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量只覆盖起止时刻，午休 ``breaks`` 沿用默认声明。"""
    monkeypatch.setenv("INGEST_WINDOW_TRADING_HOURS", "09:20-15:10")
    window = window_by_name(load_windows(), "trading_hours")
    assert (window.start, window.end) == ("09:20", "15:10")
    assert window.breaks == (("11:30", "13:00"),)


# ============================================================ 2. 任务定义


def test_limit_up_pool_task_polls_every_10_minutes_with_replace_writer() -> None:
    """任务定义：09:25-15:05 窗口 + 600s 间隔 + 替换写入器。"""
    defn = get_task("limit_up_pool")
    assert defn.interval_seconds == 600, "应为每 10 分钟一轮"
    assert defn.window is not None
    assert defn.window.name == "trading_hours"
    assert defn.target == "limit_up_pool_replace"
    # 替换写入器与幂等追加写入器是两个不同的实现
    assert WRITERS[defn.target] is not WRITERS["limit_up_pool"]


def test_trading_hours_carries_all_four_pages() -> None:
    """交易时段窗口承载 4 个页面的数据源任务，节拍与需求一致。

    需求（2026-09-21）：与涨停池同一时间段（09:25-15:05，午休跳过）内，
    主题机会每半小时一次，连板天梯与总览（情绪）每 10 分钟一次。
    """
    expected = {
        "limit_up_pool": 600,  # 涨停池：10 分钟
        "ladder": 600,  # 连板天梯：10 分钟
        "market_sentiment": 600,  # 总览（市场情绪）：10 分钟
        "theme_rank": 1800,  # 主题机会：30 分钟
        "theme_stocks": 1800,  # 主题成分股：与榜单同节拍
    }
    for name, interval in expected.items():
        defn = get_task(name)
        assert defn.window is not None, name
        assert defn.window.name == "trading_hours", name
        assert defn.interval_seconds == interval, name
        assert defn.enabled is True, name


# ============================================================ 3. 替换语义


async def test_replace_pool_drops_stale_rows(session: AsyncSession) -> None:
    """先涨停、后炸板的票在下一轮替换后不再残留（upsert 会残留，replace 不会）。"""
    repo = LimitUpPoolRepository(session)
    await repo.upsert_many([_pool_row("600001.SH"), _pool_row("600002.SH")])
    await session.commit()

    # 第二轮：600002 炸板掉出池子，只剩 600001，并新增 600003
    written = await repo.replace_pool(TRADE_DATE, [_pool_row("600001.SH"), _pool_row("600003.SH")])
    await session.commit()

    assert written == 2
    rows = await repo.get_pool(TRADE_DATE, "limit_up")
    assert {row.code for row in rows} == {"600001.SH", "600003.SH"}, "掉出池子的票应被删除"


async def test_replace_pool_leaves_other_pool_types_untouched(session: AsyncSession) -> None:
    """只替换本轮出现的 ``pool_type``，同日其他池型不受影响。"""
    repo = LimitUpPoolRepository(session)
    await repo.upsert_many(
        [_pool_row("600010.SH", pool_type="limit_up"), _pool_row("600011.SH", pool_type="broken")]
    )
    await session.commit()

    await repo.replace_pool(TRADE_DATE, [_pool_row("600020.SH", pool_type="limit_up")])
    await session.commit()

    assert {row.code for row in await repo.get_pool(TRADE_DATE, "limit_up")} == {"600020.SH"}
    assert {row.code for row in await repo.get_pool(TRADE_DATE, "broken")} == {"600011.SH"}


async def test_replace_pool_empty_rows_does_not_wipe(session: AsyncSession) -> None:
    """空结果视为「本轮无数据」：不删除、不清空当日已有快照。"""
    repo = LimitUpPoolRepository(session)
    await repo.upsert_many([_pool_row("600001.SH")])
    await session.commit()

    assert await repo.replace_pool(TRADE_DATE, []) == 0
    await session.commit()

    assert {row.code for row in await repo.get_pool(TRADE_DATE, "limit_up")} == {"600001.SH"}


async def test_replace_pool_only_touches_target_trade_date(session: AsyncSession) -> None:
    """只替换目标交易日，其他交易日的历史快照保留。"""
    repo = LimitUpPoolRepository(session)
    yesterday = TRADE_DATE - timedelta(days=1)
    await repo.upsert_many([_pool_row("600001.SH", trade_date=yesterday)])
    await repo.upsert_many([_pool_row("600001.SH", trade_date=TRADE_DATE)])
    await session.commit()

    await repo.replace_pool(TRADE_DATE, [_pool_row("600099.SH")])
    await session.commit()

    assert {row.code for row in await repo.get_pool(yesterday, "limit_up")} == {"600001.SH"}
    assert {row.code for row in await repo.get_pool(TRADE_DATE, "limit_up")} == {"600099.SH"}


# ============================================================ 4. 全交易时刻表


async def test_limit_up_pool_poll_schedule_over_a_trading_day(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """模拟一整个交易日：10 分钟节拍、午休停拉、收盘后仍有最后一刀。

    演练方式与 :meth:`IngestScheduler.run_forever` 一致（每 tick 一次
    ``run_once`` 后推进时钟），15s tick 与生产默认值对齐。
    """
    provider = PoolRecordingProvider()
    clock = {"now": _at(9, 20)}
    scheduler = IngestScheduler(
        get_settings(),
        session_factory=factory,
        provider_override=provider,
        now_fn=lambda: clock["now"],
    )

    stamps: list[str] = []
    seen = 0
    end = _at(15, 20)
    while clock["now"] <= end:
        await scheduler.run_once()
        fetched = sum(1 for capability, _ in provider.calls if capability == "limit_up_pool")
        if fetched > seen:
            seen = fetched
            stamps.append(clock["now"].strftime("%H:%M"))
        clock["now"] += timedelta(seconds=15)

    assert stamps, "整个交易日内应至少拉取一次涨停池"
    assert stamps[0] == "09:25", "首轮应在窗口起点 09:25"

    times = [datetime.strptime(stamp, "%H:%M") for stamp in stamps]

    # 午休（含 11:30 与 13:00）一律不拉
    assert all(not ("11:30" <= stamp <= "13:00") for stamp in stamps), f"午休不应拉取：{stamps}"

    # 相邻两轮间隔恒为 10 分钟，唯一的长间隔来自午休跳空
    gaps = [(later - earlier).total_seconds() for earlier, later in pairwise(times)]
    assert all(gap == 600 for gap in gaps if gap <= 600), f"节拍应为 10 分钟：{gaps}"
    assert len([gap for gap in gaps if gap > 600]) == 1, "午休应只造成一次跳空"

    # 收盘后仍有最后一刀（15:00 之后）
    assert stamps[-1] > "15:00", f"收盘后应补一刀，实际最后一轮 {stamps[-1]}"

    # 上午 09:25-11:25 共 13 轮
    morning = [stamp for stamp in stamps if stamp <= "11:25"]
    assert morning == [
        "09:25",
        "09:35",
        "09:45",
        "09:55",
        "10:05",
        "10:15",
        "10:25",
        "10:35",
        "10:45",
        "10:55",
        "11:05",
        "11:15",
        "11:25",
    ]


async def test_limit_up_pool_rounds_replace_rows_in_db(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """轮询落库走替换写入器：连续两轮后表内只留后一轮结果，且无重复行。"""
    provider = PoolRecordingProvider()
    clock = {"now": _at(9, 25)}
    scheduler = IngestScheduler(
        get_settings(),
        session_factory=factory,
        provider_override=provider,
        now_fn=lambda: clock["now"],
    )
    await scheduler.run_once()

    async with factory() as session:
        repos = LimitUpPoolRepository(session)
        first = await repos.get_pool(TRADE_DATE, "limit_up")
        assert first, "第一轮应落库"

        # 手工塞一条「本轮已掉出池子」的脏数据，验证下一轮会被替换掉
        await repos.upsert_many([_pool_row("600888.SH")])
        await session.commit()
        assert await repos.get_pool(TRADE_DATE, "limit_up")

    # 推进 10 分钟后第二轮
    clock["now"] += timedelta(seconds=600)
    await scheduler.run_once()

    async with factory() as session:
        rows = await LimitUpPoolRepository(session).get_pool(TRADE_DATE, "limit_up")
        codes = [row.code for row in rows]
    assert "600888.SH" not in codes, "上一轮的残留行应被替换写入器清掉"
    assert len(codes) == len(set(codes)), "不应出现重复行"


# ============================================================ 5. 保留策略


async def test_pool_retention_keeps_last_30_trading_days(session: AsyncSession) -> None:
    """涨停池历史只留最近 30 个交易日，更早的整日清掉。"""
    repo = LimitUpPoolRepository(session)
    days = [date(2026, 6, 1) + timedelta(days=offset) for offset in range(35)]
    for day in days:
        await repo.upsert_many([_pool_row("600001.SH", trade_date=day)])
    await session.commit()

    report = await run_retention(session=session)
    await session.commit()

    assert POOL_RETENTION_TRADING_DAYS == 30
    assert report.pools_deleted == 5, "35 天应清掉最早的 5 天"
    assert report.pool_cutoff == days[5], "保留起点应为第 30 近的交易日"

    remaining = list(
        (await session.execute(select(LimitUpPool.trade_date).distinct())).scalars().all()
    )
    assert sorted(remaining) == days[5:]


async def test_ladder_retention_keeps_last_year(session: AsyncSession) -> None:
    """连板天梯历史保留最近一年（365 自然日，用户 2026-09-22 决策）。"""
    today = datetime.now(UTC).astimezone().date()  # 与 run_retention 的「本地今日」同口径
    in_window = (today - timedelta(days=100), today - timedelta(days=10))
    out_of_window = today - timedelta(days=400)
    for day in (*in_window, out_of_window):
        session.add(
            LadderRow(
                trade_date=day,
                code="001317.SZ",
                name="三羊马",
                continue_days=2,
                first_seal_time=None,
                source="test",
            )
        )
    await session.commit()

    report = await run_retention(session=session)
    await session.commit()

    assert LADDER_RETENTION_DAYS == 365
    assert report.ladder_deleted == 1
    assert report.ladder_cutoff == today - timedelta(days=365)
    remaining = list(
        (await session.execute(select(LadderRow.trade_date).distinct())).scalars().all()
    )
    assert set(remaining) == set(in_window)


async def test_daily_bar_retention_keeps_last_60_trading_days(session: AsyncSession) -> None:
    """日线历史只留最近 60 个交易日（用户 2026-09-22 决策；不足时按自然日回退）。"""
    from app.models.market import DailyBar

    today = datetime.now(UTC).astimezone().date()  # 与 run_retention 的「本地今日」同口径
    # 库中仅 2 个交易日 → 走 45 自然日回退：46 天前的被删（严格早于起点），10 天前的保留。
    for offset in (46, 10):
        session.add(
            DailyBar(
                code="000001.SZ",
                trade_date=today - timedelta(days=offset),
                open=Decimal("10"),
                high=Decimal("10"),
                low=Decimal("10"),
                close=Decimal("10"),
                pre_close=Decimal("10"),
                volume_shares=100,
                amount_yuan=Decimal("1000"),
                source="test",
            )
        )
    await session.commit()

    report = await run_retention(session=session)
    await session.commit()

    assert DAILY_BAR_RETENTION_TRADING_DAYS == 60
    assert report.daily_bars_deleted == 1
    remaining = list(
        (await session.execute(select(DailyBar.trade_date).distinct())).scalars().all()
    )
    assert remaining == [today - timedelta(days=10)]


async def test_sentiment_retention_keeps_last_30_trading_days(session: AsyncSession) -> None:
    """市场情绪历史只留最近 30 个交易日（总览 20 日走势需要积累）。"""
    days = [date(2026, 6, 1) + timedelta(days=offset) for offset in range(35)]
    for day in days:
        session.add(
            MarketSentiment(
                trade_date=day,
                temperature=Decimal("50"),
                stage=None,
                limit_up_count=1,
                limit_down_count=0,
                broken_board_count=0,
                broken_rate=Decimal("0"),
                up_count=1,
                down_count=0,
                max_continue_days=1,
                premium_rate=Decimal("0"),
                source="test",
            )
        )
    await session.commit()

    report = await run_retention(session=session)
    await session.commit()

    assert SENTIMENT_RETENTION_TRADING_DAYS == 30
    assert report.sentiment_deleted == 5
    assert report.sentiment_cutoff == days[5]


async def test_pool_retention_falls_back_when_history_insufficient(
    session: AsyncSession,
) -> None:
    """库中交易日不足 30 天时按自然日回退，不因样本不足误删近端数据。"""
    repo = LimitUpPoolRepository(session)
    today = datetime.now(UTC).astimezone().date()
    await repo.upsert_many(
        [
            _pool_row("600001.SH", trade_date=today - timedelta(days=120)),
            _pool_row("600002.SH", trade_date=today - timedelta(days=10)),
        ]
    )
    await session.commit()

    report = await run_retention(session=session)
    await session.commit()

    assert report.pools_deleted == 1, "仅 120 天前那条应被清掉"
    assert report.pool_cutoff == today - timedelta(days=TRADING_DAY_FALLBACK_DAYS)
    codes = sorted((await session.execute(select(LimitUpPool.code))).scalars().all())
    assert codes == ["600002.SH"], "10 天前的数据应保留"
