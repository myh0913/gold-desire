"""ORM 模型测试（无需 PostgreSQL / Redis）。

在内存 SQLite（aiosqlite）上建表，验证：代表性表的读写、唯一约束、单位/长度口径
以及 JSON 列在 SQLite 上的往返。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from app.db.base import Base
from app.models import (
    AdviceReport,
    DailyBar,
    LimitUpPool,
    MinuteBar,
    RawResponse,
    StrategyConfig,
    User,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


def _daily_bar(code: str = "000001") -> DailyBar:
    """构造一条合法日线记录。"""
    return DailyBar(
        code=code,
        trade_date=TRADE_DATE,
        open=Decimal("10.0000"),
        high=Decimal("11.0000"),
        low=Decimal("9.5000"),
        close=Decimal("10.5000"),
        pre_close=Decimal("10.0000"),
        volume_shares=123_456_789,
        amount_yuan=Decimal("1300000000.00"),
        source="xuangutong",
    )


async def test_insert_and_query_representative_tables(session: AsyncSession) -> None:
    """六个模块各取一张代表性表完成写入与回读。"""
    session.add(User(username="admin", password_hash="argon2id$hash", role="admin", enabled=True))
    session.add(_daily_bar())
    session.add(
        LimitUpPool(
            trade_date=TRADE_DATE,
            code="000001",
            name="平安银行",
            continue_days=3,
            limit_up_time="09:31",
            seal_amount_yuan=Decimal("52000000.00"),
            open_times=1,
            turnover_rate=Decimal("0.0812"),
            amount_yuan=Decimal("1300000000.00"),
            market_cap_yuan=Decimal("200000000000.00"),
            pool_type="limit_up",
            source="xuangutong",
        )
    )
    session.add(
        StrategyConfig(
            strategy_id="dragon_retrace",
            version=1,
            status="active",
            params={"amplitude": 0.08},
        )
    )
    session.add(
        AdviceReport(
            trade_date=TRADE_DATE,
            kind="intraday",
            strategy_id="dragon_retrace",
            strategy_version=1,
            payload={"picks": []},
            ran_at=datetime(2026, 9, 18, 9, 35, tzinfo=UTC),
        )
    )
    session.add(
        RawResponse(
            source="xuangutong",
            capability="limit_up_pool",
            args_hash="a" * 16,
            trade_date=TRADE_DATE,
            payload={"raw": 1},
            sha256="b" * 64,
            http_status=200,
            elapsed_ms=42,
        )
    )
    await session.commit()

    user = (await session.execute(select(User).where(User.username == "admin"))).scalar_one()
    assert user.role == "admin"
    assert user.enabled is True

    bar = (await session.execute(select(DailyBar))).scalar_one()
    assert bar.close == Decimal("10.5000")
    assert bar.volume_shares == 123_456_789

    pool = (await session.execute(select(LimitUpPool))).scalar_one()
    assert pool.pool_type == "limit_up"
    assert pool.continue_days == 3

    config = (await session.execute(select(StrategyConfig))).scalar_one()
    assert config.status == "active"

    advice = (await session.execute(select(AdviceReport))).scalar_one()
    assert advice.strategy_version == 1

    raw = (await session.execute(select(RawResponse))).scalar_one()
    assert raw.http_status == 200
    assert raw.elapsed_ms == 42


async def test_daily_bar_unique_constraint(session: AsyncSession) -> None:
    """``(code, trade_date)`` 唯一约束应拒绝重复插入。"""
    session.add(_daily_bar())
    await session.commit()

    session.add(_daily_bar())
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_limit_up_pool_unique_constraint(session: AsyncSession) -> None:
    """``(trade_date, pool_type, code)`` 唯一约束应拒绝重复插入。"""

    def build() -> LimitUpPool:
        return LimitUpPool(
            trade_date=TRADE_DATE,
            code="000001",
            name="平安银行",
            continue_days=1,
            seal_amount_yuan=Decimal("1.00"),
            open_times=0,
            turnover_rate=Decimal("0.0100"),
            amount_yuan=Decimal("1.00"),
            market_cap_yuan=Decimal("1.00"),
            pool_type="broken",
            source="eastmoney",
        )

    session.add(build())
    await session.commit()

    session.add(build())
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_time_labels_are_five_chars(session: AsyncSession) -> None:
    """``minute_bars.time_label`` 与 ``limit_up_pool.limit_up_time`` 为 5 字符 ``HH:MM``。"""
    session.add(
        MinuteBar(
            code="000001",
            trade_date=TRADE_DATE,
            minute_index=0,
            time_label="09:30",
            price=Decimal("10.5000"),
            volume_lots=1_234,
            amount_yuan=Decimal("1300000.00"),
            source="xuangutong",
        )
    )
    session.add(
        LimitUpPool(
            trade_date=TRADE_DATE,
            code="000002",
            name="万科A",
            continue_days=1,
            limit_up_time="09:31",
            seal_amount_yuan=Decimal("1.00"),
            open_times=0,
            turnover_rate=Decimal("0.0100"),
            amount_yuan=Decimal("1.00"),
            market_cap_yuan=Decimal("1.00"),
            pool_type="limit_up",
            source="eastmoney",
        )
    )
    await session.commit()

    minute = (await session.execute(select(MinuteBar))).scalar_one()
    assert isinstance(minute.time_label, str)
    assert len(minute.time_label) == 5
    assert minute.time_label == "09:30"

    pool = (await session.execute(select(LimitUpPool))).scalar_one()
    assert pool.limit_up_time is not None
    assert len(pool.limit_up_time) == 5

    assert MinuteBar.__table__.c.time_label.type.length == 5  # type: ignore[attr-defined]
    assert LimitUpPool.__table__.c.limit_up_time.type.length == 5  # type: ignore[attr-defined]


async def test_json_columns_round_trip(session: AsyncSession) -> None:
    """JSON 列在 SQLite 上应完整往返 dict / list。"""
    params = {"amplitude": 0.08, "nested": {"levels": [1, 2, 3]}, "label": "首阴"}
    session.add(
        StrategyConfig(strategy_id="dragon_retrace", version=2, status="draft", params=params)
    )
    session.add(
        RawResponse(
            source="xuangutong",
            capability="news_flash",
            args_hash="c" * 16,
            payload={"items": [{"title": "快讯", "symbols": ["000001"]}]},
            sha256="d" * 64,
        )
    )
    await session.commit()
    session.expire_all()

    config = (await session.execute(select(StrategyConfig))).scalar_one()
    assert config.params == params
    assert config.params["nested"]["levels"] == [1, 2, 3]

    raw = (await session.execute(select(RawResponse))).scalar_one()
    assert raw.payload["items"][0]["symbols"] == ["000001"]


def test_all_modules_registered() -> None:
    """五个领域模块的表都应注册进 metadata（32 张）。"""
    tables = set(Base.metadata.tables)
    assert {
        "users",
        "daily_bars",
        "limit_up_pool",
        "strategy_configs",
        "advice_reports",
        "advice_marks",
        "raw_responses",
        "cycle_judgements",
        "dragon_pool",
    } <= tables
    assert len(tables) == 32
