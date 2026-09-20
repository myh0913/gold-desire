"""仓储层测试（SQLite / aiosqlite，无需 PostgreSQL 与 Redis）。

覆盖：幂等 upsert、强制有界分页、配置版本生命周期与回滚、参数 diff、
保留策略清理、情绪历史排序、快讯关键词检索、天梯区间过滤。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.db.base import Base
from app.models.auth import AuditLog
from app.models.derived import AdviceReport
from app.models.market import DailyBar, LimitUpPool, MinuteBar
from app.models.raw import RawResponse
from app.repositories import (
    DailyBarRepository,
    LadderRepository,
    LimitUpPoolRepository,
    MarketSentimentRepository,
    MinuteBarRepository,
    NewsFlashRepository,
    Repositories,
    StrategyConfigRepository,
    get_repositories,
)
from app.repositories.retention import run_retention
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

TRADE_DATE = date(2026, 9, 18)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 会话；每个用例独立建库以保证隔离。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


# --------------------------------------------------------------------- 构造器


def _daily_row(code: str, trade_date: date = TRADE_DATE, close: str = "10.5000") -> dict[str, Any]:
    """构造一条合法日线行字典。"""
    return {
        "code": code,
        "trade_date": trade_date,
        "open": Decimal("10.0000"),
        "high": Decimal("11.0000"),
        "low": Decimal("9.5000"),
        "close": Decimal(close),
        "pre_close": Decimal("10.0000"),
        "volume_shares": 1_000_000,
        "amount_yuan": Decimal("1000000.00"),
        "source": "xuangutong",
    }


def _limit_row(code: str, continue_days: int = 1, pool_type: str = "limit_up") -> dict[str, Any]:
    """构造一条合法涨停池行字典。"""
    return {
        "trade_date": TRADE_DATE,
        "code": code,
        "name": f"股票{code}",
        "continue_days": continue_days,
        "limit_up_time": "09:31",
        "seal_amount_yuan": Decimal("1.00"),
        "open_times": 0,
        "turnover_rate": Decimal("0.0100"),
        "amount_yuan": Decimal("1.00"),
        "market_cap_yuan": Decimal("1.00"),
        "pool_type": pool_type,
        "source": "xuangutong",
    }


def _sentiment_row(trade_date: date, temperature: str = "50.0000") -> dict[str, Any]:
    """构造一条合法情绪行字典。"""
    return {
        "trade_date": trade_date,
        "temperature": Decimal(temperature),
        "stage": "启动",
        "limit_up_count": 30,
        "limit_down_count": 3,
        "broken_board_count": 5,
        "broken_rate": Decimal("0.1000"),
        "up_count": 2000,
        "down_count": 1000,
        "max_continue_days": 4,
        "premium_rate": Decimal("0.0200"),
        "source": "xuangutong",
    }


async def _row_count(session: AsyncSession, model: type[Any]) -> int:
    """统计某表行数。"""
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


# --------------------------------------------------------------- 1. 幂等 upsert


async def test_bulk_upsert_idempotent_daily_bar(session: AsyncSession) -> None:
    """同一键集合重复 upsert：行数不变、值被覆盖。"""
    repo = DailyBarRepository(session)
    first = [_daily_row("000001", close="10.5000"), _daily_row("000002", close="20.0000")]
    assert await repo.upsert_many(first) == 2
    await session.commit()
    assert await _row_count(session, DailyBar) == 2

    second = [_daily_row("000001", close="11.5000"), _daily_row("000002", close="21.0000")]
    assert await repo.upsert_many(second) == 2
    await session.commit()

    assert await _row_count(session, DailyBar) == 2
    latest = await repo.get_latest("000001")
    assert latest is not None
    assert latest.close == Decimal("11.5000")
    bar = await repo.get_by_date(TRADE_DATE, codes=["000002"])
    assert len(bar) == 1
    assert bar[0].close == Decimal("21.0000")


async def test_bulk_upsert_idempotent_limit_up_pool(session: AsyncSession) -> None:
    """涨停池重复 upsert：唯一键不变、连板天数被覆盖。"""
    repo = LimitUpPoolRepository(session)
    assert await repo.upsert_many([_limit_row("000001", continue_days=1)]) == 1
    await session.commit()
    assert await _row_count(session, LimitUpPool) == 1

    assert await repo.upsert_many([_limit_row("000001", continue_days=3)]) == 1
    await session.commit()

    assert await _row_count(session, LimitUpPool) == 1
    pool = await repo.get_pool(TRADE_DATE, "limit_up")
    assert len(pool) == 1
    assert pool[0].continue_days == 3


# ------------------------------------------------------------------- 2. 分页


async def test_paginate_enforces_bounds(session: AsyncSession) -> None:
    """默认页大小 20、超限收敛到 200、total/pages 正确。"""
    repo = DailyBarRepository(session)
    await repo.upsert_many([_daily_row(f"{index:06d}") for index in range(25)])
    await session.commit()

    stmt = select(DailyBar).order_by(DailyBar.code)
    default_page = await repo.paginate(stmt)
    assert default_page.page_size == 20
    assert default_page.total == 25
    assert default_page.pages == 2
    assert len(default_page.items) == 20

    second_page = await repo.paginate(stmt, page=2)
    assert len(second_page.items) == 5
    assert second_page.page == 2

    clamped = await repo.paginate(stmt, page_size=500)
    assert clamped.page_size == 200
    assert clamped.pages == 1
    assert len(clamped.items) == 25


# --------------------------------------------------------- 3. 版本生命周期


async def test_version_lifecycle_and_rollback(session: AsyncSession) -> None:
    """draft → active 唯一；再次 activate 归档旧版；回滚新建 active 版本。"""
    repo = StrategyConfigRepository(session)

    draft1 = await repo.create_draft("dragon", {"amplitude": 0.08}, "初始")
    assert draft1.version == 1
    assert draft1.status == "draft"

    activated1 = await repo.activate("dragon", 1)
    assert activated1.status == "active"
    active = await repo.get_active("dragon")
    assert active is not None and active.version == 1

    await repo.create_draft("dragon", {"amplitude": 0.07}, "调参")
    await repo.activate("dragon", 2)
    versions = await repo.list_versions("dragon")
    actives = [row for row in versions if row.status == "active"]
    assert len(actives) == 1
    assert actives[0].version == 2
    archived = await repo.get_version("dragon", 1)
    assert archived is not None and archived.status == "archived"

    rolled = await repo.rollback("dragon", 1)
    assert rolled.version == 3
    assert rolled.status == "active"
    assert rolled.params == {"amplitude": 0.08}
    assert await _row_count(session, type(rolled)) == 3
    actives = [row for row in await repo.list_versions("dragon") if row.status == "active"]
    assert len(actives) == 1
    assert actives[0].version == 3


# --------------------------------------------------------------------- 4. diff


async def test_diff_versions_reports_added_removed_changed(session: AsyncSession) -> None:
    """diff 应正确报告新增 / 删除 / 变更的参数。"""
    repo = StrategyConfigRepository(session)
    await repo.create_draft("dragon", {"a": 1, "b": 2, "c": 3}, "v1")
    await repo.activate("dragon", 1)
    await repo.create_draft("dragon", {"a": 1, "b": 9, "d": 4}, "v2")

    diff = await repo.diff_versions("dragon", 1, 2)
    assert diff.added == {"d": 4}
    assert diff.removed == {"c": 3}
    assert diff.changed == {"b": (2, 9)}
    assert not diff.is_empty()

    same = await repo.diff_versions("dragon", 1, 1)
    assert same.is_empty()


# ----------------------------------------------------------------- 5. 保留策略


async def test_run_retention_deletes_only_expired(session: AsyncSession) -> None:
    """raw 超 30 天、分时超 90 天被删；日线与建议报告永久保留。"""
    now = datetime.now(UTC)
    session.add(
        RawResponse(
            source="xuangutong",
            capability="limit_up_pool",
            args_hash="old" * 10,
            trade_date=TRADE_DATE,
            payload={"raw": "old"},
            sha256="a" * 64,
            fetched_at=now - timedelta(days=45),
        )
    )
    session.add(
        RawResponse(
            source="xuangutong",
            capability="limit_up_pool",
            args_hash="new" * 10,
            trade_date=TRADE_DATE,
            payload={"raw": "new"},
            sha256="b" * 64,
            fetched_at=now - timedelta(days=2),
        )
    )

    minute_repo = MinuteBarRepository(session)
    old_minute_date = (now - timedelta(days=120)).date()
    new_minute_date = (now - timedelta(days=10)).date()
    await minute_repo.upsert_many(
        [
            {
                "code": "000001",
                "trade_date": old_minute_date,
                "minute_index": 0,
                "time_label": "09:30",
                "price": Decimal("10.0000"),
                "volume_lots": 10,
                "amount_yuan": Decimal("1000.00"),
                "source": "xuangutong",
            },
            {
                "code": "000001",
                "trade_date": new_minute_date,
                "minute_index": 0,
                "time_label": "09:30",
                "price": Decimal("10.0000"),
                "volume_lots": 10,
                "amount_yuan": Decimal("1000.00"),
                "source": "xuangutong",
            },
        ]
    )
    await DailyBarRepository(session).upsert_many(
        [_daily_row("000001", trade_date=old_minute_date)]
    )
    session.add(
        AdviceReport(
            trade_date=old_minute_date,
            kind="intraday",
            strategy_id="dragon",
            strategy_version=1,
            payload={"picks": []},
            ran_at=now - timedelta(days=120),
        )
    )
    await session.commit()

    report = await run_retention(session=session)

    assert report.raw_responses_deleted == 1
    assert report.minute_bars_deleted == 1
    assert await _row_count(session, RawResponse) == 1
    assert await _row_count(session, MinuteBar) == 1
    # 永久表不受影响
    assert await _row_count(session, DailyBar) == 1
    assert await _row_count(session, AdviceReport) == 1


# --------------------------------------------------------------- 6. 情绪历史


async def test_sentiment_history_is_ascending(session: AsyncSession) -> None:
    """get_history 按交易日升序返回，且天数据限正确。"""
    repo = MarketSentimentRepository(session)
    dates = [date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18)]
    await repo.upsert_many(
        [_sentiment_row(day, temperature=f"{50 + index}.0000") for index, day in enumerate(dates)]
    )
    await session.commit()

    history = await repo.get_history(10)
    assert [row.trade_date for row in history] == dates

    recent = await repo.get_history(2)
    assert [row.trade_date for row in recent] == dates[-2:]
    assert recent[-1].trade_date == dates[-1]


# --------------------------------------------------------------- 7. 快讯检索


async def test_news_flash_keyword_searches_title_and_summary(session: AsyncSession) -> None:
    """关键词应同时命中标题与摘要。"""
    repo = NewsFlashRepository(session)
    base_ts = datetime(2026, 9, 18, 9, 30, tzinfo=UTC)
    await repo.upsert_many(
        [
            {
                "ts": base_ts,
                "level": "high",
                "title": "涨停潮来袭",
                "summary": "多股封板",
                "symbols": ["000001"],
                "categories": ["市场"],
                "source": "xuangutong",
            },
            {
                "ts": base_ts + timedelta(minutes=5),
                "level": "normal",
                "title": "公司公告",
                "summary": "拟进行重大资产重组",
                "symbols": ["000002"],
                "categories": ["公司"],
                "source": "eastmoney",
            },
            {
                "ts": base_ts + timedelta(minutes=10),
                "level": "normal",
                "title": "无关资讯",
                "summary": "天气晴朗",
                "symbols": [],
                "categories": [],
                "source": "eastmoney",
            },
        ]
    )
    await session.commit()

    by_title = await repo.list_recent(10, keyword="涨停")
    assert len(by_title) == 1
    assert by_title[0].title == "涨停潮来袭"

    by_summary = await repo.list_recent(10, keyword="重组")
    assert len(by_summary) == 1
    assert by_summary[0].symbols == ["000002"]

    by_level = await repo.list_recent(10, level="high")
    assert len(by_level) == 1

    assert len(await repo.list_recent(10)) == 3


# --------------------------------------------------------------- 8. 天梯过滤


async def test_ladder_range_filters_min_continue_days(session: AsyncSession) -> None:
    """区间查询应过滤掉低于 min_continue_days 的记录。"""
    repo = LadderRepository(session)
    rows: list[Mapping[str, Any]] = []
    for offset, heights in enumerate(([1, 2, 3], [2, 4])):
        day = date(2026, 9, 17 + offset)
        for index, height in enumerate(heights):
            rows.append(
                {
                    "trade_date": day,
                    "code": f"{offset}{index:05d}",
                    "name": f"股票{offset}{index}",
                    "continue_days": height,
                    "first_seal_time": "09:31",
                    "source": "xuangutong",
                }
            )
    await repo.upsert_many(rows)
    await session.commit()

    filtered = await repo.get_range(date(2026, 9, 17), date(2026, 9, 18), min_continue_days=2)
    assert len(filtered) == 4
    assert all(row.continue_days >= 2 for row in filtered)
    # 交易日升序、同日内连板高度降序
    assert [row.continue_days for row in filtered] == [3, 2, 4, 2]

    all_rows = await repo.get_range(date(2026, 9, 17), date(2026, 9, 18), min_continue_days=1)
    assert len(all_rows) == 5
    assert await repo.available_dates(10) == [date(2026, 9, 18), date(2026, 9, 17)]


# ------------------------------------------------------- 容器 / 依赖注入装配


async def test_repositories_container_bundles_all(session: AsyncSession) -> None:
    """容器应聚合全部仓储，并暴露 FastAPI 依赖。"""
    container = Repositories.build(session)
    assert container.session is session
    assert isinstance(container.daily_bars, DailyBarRepository)
    assert callable(get_repositories)
    # 审计日志仓储可用
    await container.audit_logs.record("admin", "login", target="admin", ip="127.0.0.1")
    await session.commit()
    assert await _row_count(session, AuditLog) == 1
