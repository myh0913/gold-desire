"""连板天梯矩阵口径测试（SQLite / aiosqlite，零网络）。

覆盖 `MarketService.ladder_matrix`：列=交易日升序、行=个股按代码聚合、
单元格=连板数 + 首封时间、`min_continue_days` 过滤、区间缺省取最近 N 个交易日。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta

import pytest
from app.db.base import Base
from app.models.market import LadderRow
from app.repositories import Repositories
from app.services.market_service import MarketService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

#: 一周的「交易日」（用连续自然日代替，矩阵只看 trade_date 值）。
DAYS = [date(2026, 9, 14) + timedelta(days=offset) for offset in range(5)]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 会话；每例独立建库以保证隔离。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
        yield opened
    await engine.dispose()


async def _seed(session: AsyncSession) -> None:
    """种入天梯：甲票连板逐日递增（2→6 板），乙票只在最后一天 1 板（应被下界滤掉）。"""
    session.add_all(
        [
            LadderRow(
                trade_date=day,
                code="000001.SZ",
                name="甲票",
                continue_days=2 + index,
                first_seal_time=f"09:{30 + index:02d}",
                source="test",
            )
            for index, day in enumerate(DAYS)
        ]
    )
    session.add(
        LadderRow(
            trade_date=DAYS[-1],
            code="000002.SZ",
            name="乙票",
            continue_days=1,
            first_seal_time="10:00",
            source="test",
        )
    )
    await session.commit()


async def test_ladder_matrix_shape(session: AsyncSession) -> None:
    """矩阵：交易日升序、按个股聚合、单元格含连板数与首封时间。"""
    await _seed(session)
    service = MarketService(Repositories.build(session))
    result = await service.ladder_matrix(
        start=None, end=None, min_continue_days=2, limit_days=30
    )

    assert result.days == [day.isoformat() for day in DAYS], "交易日应按升序"
    assert [row.code for row in result.rows] == ["000001.SZ"], "1 板的乙票应被下界滤掉"
    assert result.min_continue_days == 2

    row = result.rows[0]
    assert row.name == "甲票"
    assert sorted(row.cells) == [day.isoformat() for day in DAYS]
    assert [row.cells[day.isoformat()].boards for day in DAYS] == [2, 3, 4, 5, 6]
    assert row.cells[DAYS[0].isoformat()].first_seal_time == "09:30"
    assert row.cells[DAYS[-1].isoformat()].first_seal_time == "09:34"


async def test_ladder_matrix_min_continue_days_filter(session: AsyncSession) -> None:
    """下界可调：设为 1 时 1 板个股也纳入。"""
    await _seed(session)
    service = MarketService(Repositories.build(session))
    result = await service.ladder_matrix(
        start=None, end=None, min_continue_days=1, limit_days=30
    )
    assert [row.code for row in result.rows] == ["000001.SZ", "000002.SZ"]
    only_last = [day for day in result.days if day == DAYS[-1].isoformat()]
    assert only_last == [DAYS[-1].isoformat()]


async def test_ladder_matrix_explicit_range_limits_days(session: AsyncSession) -> None:
    """显式区间只返回区间内的交易日。"""
    await _seed(session)
    service = MarketService(Repositories.build(session))
    result = await service.ladder_matrix(
        start=DAYS[1], end=DAYS[2], min_continue_days=2, limit_days=30
    )
    assert result.days == [DAYS[1].isoformat(), DAYS[2].isoformat()]
    assert result.rows[0].cells[DAYS[1].isoformat()].boards == 3


async def test_ladder_matrix_limit_days_takes_recent_window(session: AsyncSession) -> None:
    """未指定区间时取最近 ``limit_days`` 个交易日。"""
    await _seed(session)
    service = MarketService(Repositories.build(session))
    result = await service.ladder_matrix(
        start=None, end=None, min_continue_days=2, limit_days=2
    )
    assert result.days == [DAYS[-2].isoformat(), DAYS[-1].isoformat()]


async def test_ladder_matrix_empty_when_no_rows(session: AsyncSession) -> None:
    """库中无天梯数据时返回空矩阵（不报错）。"""
    service = MarketService(Repositories.build(session))
    result = await service.ladder_matrix(
        start=None, end=None, min_continue_days=2, limit_days=30
    )
    assert result.days == []
    assert result.rows == []
