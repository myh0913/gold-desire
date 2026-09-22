"""认证/安全测试共享夹具。

在内存 SQLite（StaticPool，跨请求共享同一库）上建表，覆盖 ``get_session`` 依赖，
并提供内置角色种子、用户构造器与登录辅助。全程无需 PostgreSQL / Redis。

另提供 Task 12（读 API）所需的行情种子夹具（:func:`market_seed`）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from app.core.cache import MemoryCache, get_cache
from app.core.pages import reset_registry
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models.auth import User
from app.repositories import Repositories
from app.services.cache_policy import reset_cache_policy
from app.services.role_service import RoleService
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

TEST_PASSWORD = "Passw0rd1"

#: 读 API 种子数据的基准交易日。
TRADE_DATE = date(2026, 6, 3)
#: 龙回头样本基准日 D（三段各一条，保证 A/B/C 均有样本）。
SAMPLE_DATES: tuple[date, date, date] = (date(2026, 1, 7), date(2026, 3, 4), date(2026, 6, 3))
#: 样本窗口。
SAMPLE_START = date(2026, 1, 1)
SAMPLE_END = date(2026, 6, 30)
#: 主测试股票代码。
STOCK_CODE = "600001"
#: 预置回测任务 ID。
BACKTEST_RUN_ID = "run-test-0001"


@pytest.fixture(autouse=True)
def _reset_page_registry() -> Iterator[None]:
    """每个用例后把页面注册表恢复为内置集合。"""
    yield
    reset_registry()


@pytest.fixture(autouse=True)
async def _reset_read_cache() -> AsyncIterator[None]:
    """每个用例前后重置两级缓存：策略单例 + 进程级 L2 内存后端。

    只 ``reset_cache_policy()`` 不够——L2（``get_cache()``）是进程级单例，
    前一个用例经 ``get_or_load`` 写入的条目会跨用例/跨文件串台（如天梯矩阵
    「空库」用例命中早前测试缓存的非空结果）。
    """
    reset_cache_policy()
    cache = get_cache()
    if isinstance(cache, MemoryCache):
        await cache.clear()
    yield
    reset_cache_policy()
    if isinstance(cache, MemoryCache):
        await cache.clear()


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """内存 SQLite 引擎（StaticPool：同一连接，跨请求共享数据）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """绑定测试引擎的会话工厂。"""
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def app(engine: AsyncEngine) -> AsyncIterator[FastAPI]:
    """测试应用：覆盖 ``get_session``，并种入内置角色、清空缓存。"""
    application = create_app()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # 暴露给 DI 层（如 ReportService 后台回测任务复用同一内存库）。
    application.state.session_factory = factory

    async def _override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_session] = _override
    async with factory() as session:
        await RoleService(Repositories.build(session)).ensure_builtin_roles()
        await session.commit()

    cache = get_cache()
    if isinstance(cache, MemoryCache):
        await cache.clear()
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """基于 ASGI 的异步客户端；用 https 以便携带 Secure refresh cookie。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as async_client:
        yield async_client


@pytest.fixture
async def make_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., Awaitable[User]]:
    """构造并落库一个用户，返回该用户。"""

    async def _make(
        username: str,
        password: str = TEST_PASSWORD,
        role: str = "viewer",
        enabled: bool = True,
    ) -> User:
        async with session_factory() as session:
            user = User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                enabled=enabled,
            )
            session.add(user)
            await session.commit()
            return user

    return _make


@pytest.fixture
async def login(client: httpx.AsyncClient) -> Callable[..., Awaitable[str]]:
    """登录并返回 access token。"""

    async def _login(username: str, password: str = TEST_PASSWORD) -> str:
        response = await client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 200, response.text
        return str(response.json()["access_token"])

    return _login


def auth_header(token: str) -> dict[str, str]:
    """构造 Bearer 授权头。"""
    return {"Authorization": f"Bearer {token}"}


# ============================================================ 读 API 种子数据


def _bar(
    code: str,
    day: date,
    pre_close: str,
    o: str,
    h: str,
    low: str,
    close: str,
    volume: int = 1_000_000,
) -> dict[str, Any]:
    """构造一条日线行。"""
    return {
        "code": code,
        "trade_date": day,
        "open": Decimal(o),
        "high": Decimal(h),
        "low": Decimal(low),
        "close": Decimal(close),
        "pre_close": Decimal(pre_close),
        "volume_shares": volume,
        "amount_yuan": Decimal("1000000.00"),
        "source": "fake",
    }


def _dragon_bars(code: str) -> list[dict[str, Any]]:
    """构造 3 组「2 连板 + 紧邻首阴 + T + T1」，供样本构建与有效性统计使用。"""
    patterns = (
        ("10.0", "10.2", "11.0", "10.1", "11.0"),
        ("11.0", "11.1", "12.1", "11.0", "12.1"),
        ("12.1", "12.5", "12.6", "11.0", "11.5"),
        ("11.5", "11.6", "12.0", "11.4", "11.8"),
        ("11.8", "11.9", "12.2", "11.7", "12.0"),
        ("12.0", "12.1", "13.2", "12.0", "13.2"),
        ("13.2", "13.3", "14.52", "13.2", "14.52"),
        ("14.52", "14.8", "15.0", "13.5", "13.9"),
        ("13.9", "14.0", "14.5", "13.8", "14.3"),
        ("14.3", "14.4", "14.8", "14.2", "14.6"),
        ("14.6", "14.7", "16.06", "14.6", "16.06"),
        ("16.06", "16.1", "17.666", "16.0", "17.666"),
        ("17.666", "17.9", "18.1", "16.5", "16.9"),
        ("16.9", "17.0", "17.5", "16.8", "17.3"),
        ("17.3", "17.4", "17.8", "17.2", "17.6"),
    )
    days = (
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
        date(2026, 3, 2),
        date(2026, 3, 3),
        date(2026, 3, 4),
        date(2026, 3, 5),
        date(2026, 3, 6),
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 4),
        date(2026, 6, 5),
    )
    return [
        _bar(code, day, pre_close, o, h, low, close)
        for day, (pre_close, o, h, low, close) in zip(days, patterns, strict=True)
    ]


def _minute_rows(code: str, day: date) -> list[dict[str, Any]]:
    """构造某日的分时行（5 个点）。"""
    prices = ["12.50", "12.30", "12.60", "11.90", "11.50"]
    return [
        {
            "code": code,
            "trade_date": day,
            "minute_index": index,
            "time_label": f"09:{31 + index:02d}",
            "price": Decimal(price),
            "volume_lots": 100,
            "amount_yuan": Decimal("100000.00"),
            "source": "fake",
        }
        for index, price in enumerate(prices)
    ]


def _pool_row(code: str, pool_type: str, continue_days: int = 2) -> dict[str, Any]:
    """构造一条涨停池行。"""
    return {
        "trade_date": TRADE_DATE,
        "code": code,
        "name": f"测试股{code[-3:]}",
        "continue_days": continue_days,
        "limit_up_time": "09:31",
        "seal_amount_yuan": Decimal("1000000.00"),
        "open_times": 0,
        "turnover_rate": Decimal("0.0812"),
        "amount_yuan": Decimal("2000000.00"),
        "market_cap_yuan": Decimal("3000000000.00"),
        "pool_type": pool_type,
        "source": "fake",
    }


def _sentiment_row(day: date, temperature: str = "62.5000") -> dict[str, Any]:
    """构造一条情绪行。"""
    return {
        "trade_date": day,
        "temperature": Decimal(temperature),
        "stage": "加速/高潮",
        "limit_up_count": 42,
        "limit_down_count": 3,
        "broken_board_count": 6,
        "broken_rate": Decimal("0.1250"),
        "up_count": 2600,
        "down_count": 1400,
        "max_continue_days": 5,
        "premium_rate": Decimal("0.0250"),
        "source": "fake",
    }


async def seed_market(factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """种入覆盖全部读接口的最小行情/报告/配置数据集。"""
    async with factory() as session:
        repos = Repositories.build(session)

        await repos.stocks.upsert_many(
            [
                {
                    "code": STOCK_CODE,
                    "name": "测试一号",
                    "market": "SH",
                    "board": "主板",
                    "is_st": False,
                    "list_date": date(2015, 1, 1),
                    "source": "fake",
                },
                {
                    "code": "600002",
                    "name": "测试二号",
                    "market": "SH",
                    "board": "主板",
                    "is_st": False,
                    "list_date": None,
                    "source": "fake",
                },
            ]
        )
        await repos.daily_bars.upsert_many(_dragon_bars(STOCK_CODE))
        minute_rows: list[dict[str, Any]] = []
        for day in SAMPLE_DATES:
            minute_rows.extend(_minute_rows(STOCK_CODE, day))
        await repos.minute_bars.upsert_many(minute_rows)

        await repos.limit_up_pool.upsert_many(
            [
                _pool_row(STOCK_CODE, "limit_up", continue_days=3),
                _pool_row("600002", "limit_up", continue_days=2),
                _pool_row("600002", "broken", continue_days=1),
            ]
        )
        await repos.ladder.upsert_many(
            [
                {
                    "trade_date": TRADE_DATE,
                    "code": STOCK_CODE,
                    "name": "测试一号",
                    "continue_days": 3,
                    "first_seal_time": "09:31",
                    "source": "fake",
                },
                {
                    "trade_date": TRADE_DATE,
                    "code": "600002",
                    "name": "测试二号",
                    "continue_days": 2,
                    "first_seal_time": "09:35",
                    "source": "fake",
                },
            ]
        )
        await repos.market_sentiment.upsert_many(
            [
                _sentiment_row(date(2026, 6, 1), "55.0000"),
                _sentiment_row(date(2026, 6, 2), "58.0000"),
                _sentiment_row(TRADE_DATE),
            ]
        )
        await repos.themes.upsert_many(
            themes=[
                {
                    "trade_date": TRADE_DATE,
                    "rank": 1,
                    "name": "AI",
                    "core_avg_pct": Decimal("0.0521"),
                    "description": "人工智能",
                    "core_count": 2,
                    "source": "fake",
                }
            ],
            stocks=[
                {
                    "trade_date": TRADE_DATE,
                    "theme_name": "AI",
                    "code": STOCK_CODE,
                    "name": "测试一号",
                    "price": Decimal("16.9000"),
                    "pct": Decimal("0.0521"),
                    "turnover_rate": Decimal("0.0812"),
                    "continue_days": 3,
                    "source": "fake",
                }
            ],
        )
        await repos.news_flash.upsert_many(
            [
                {
                    "ts": datetime(2026, 6, 3, 9, 0, tzinfo=UTC),
                    "title": "测试快讯一",
                    "summary": "摘要一",
                    "symbols": [STOCK_CODE],
                    "categories": ["宏观"],
                    "source": "fake",
                },
                {
                    "ts": datetime(2026, 6, 3, 10, 0, tzinfo=UTC),
                    "title": "测试快讯二",
                    "summary": None,
                    "symbols": [],
                    "categories": [],
                    "source": "fake",
                },
            ]
        )
        await repos.monitor_stocks.upsert_many(
            [
                {
                    "trade_date": TRADE_DATE,
                    "kind": "restricted",
                    "code": STOCK_CODE,
                    "name": "测试一号",
                    "reason": "重点监控",
                    "source": "fake",
                }
            ]
        )
        await repos.advice_reports.upsert_many(
            [
                {
                    "trade_date": TRADE_DATE,
                    "kind": "advice",
                    "strategy_id": "dragon",
                    "strategy_version": 1,
                    "code": STOCK_CODE,
                    "payload": {"path_id": "S2", "code": STOCK_CODE, "position": 0.2},
                    "ran_at": datetime(2026, 6, 3, 15, 5, tzinfo=UTC),
                }
            ]
        )
        await repos.backtest_runs.create(
            BACKTEST_RUN_ID, SAMPLE_START, SAMPLE_END, ["dragon"], {"base_position": 0.2}
        )
        await repos.backtest_runs.update_status(
            BACKTEST_RUN_ID, "succeeded", report={"segments": [{"name": "A"}]}
        )
        await repos.datasource_registry.upsert(
            "fake", "Fake", "fake", ["daily_bars", "limit_up_pool"], priority=100
        )
        await repos.ingest_jobs.start("job-test-0001", "daily_bars", "fake", TRADE_DATE)
        await repos.ingest_jobs.finish("job-test-0001", "succeeded", 15)

        from app.factors.registry import sync_definitions as sync_factors
        from app.strategies.registry import sync_definitions as sync_strategies

        await sync_strategies(repos)
        await sync_factors(repos)

        # 各留两个参数版本，供版本列表 / diff 读接口使用。
        draft = await repos.strategy_configs.create_draft(
            "dragon", {"base_position": 0.20}, "v1", created_by="seed"
        )
        await repos.strategy_configs.activate("dragon", int(draft.version))
        draft = await repos.strategy_configs.create_draft(
            "dragon", {"base_position": 0.25}, "v2", created_by="seed"
        )
        await repos.strategy_configs.activate("dragon", int(draft.version))

        draft = await repos.factor_configs.create_draft(
            "first_yin_amplitude", {"min_amplitude": 0.08}, "v1", created_by="seed"
        )
        await repos.factor_configs.activate("first_yin_amplitude", int(draft.version))
        draft = await repos.factor_configs.create_draft(
            "first_yin_amplitude", {"min_amplitude": 0.07}, "v2", created_by="seed"
        )
        await repos.factor_configs.activate("first_yin_amplitude", int(draft.version))

        await session.commit()

    return {
        "trade_date": TRADE_DATE,
        "sample_dates": SAMPLE_DATES,
        "sample_start": SAMPLE_START,
        "sample_end": SAMPLE_END,
        "code": STOCK_CODE,
        "run_id": BACKTEST_RUN_ID,
    }


@pytest.fixture
async def market_seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """种入读接口数据集并返回关键标识（日期 / 代码 / run_id）。"""
    return await seed_market(session_factory)


@pytest.fixture
async def admin_token(
    make_user: Callable[..., Awaitable[User]], login: Callable[..., Awaitable[str]]
) -> str:
    """创建一个 admin 用户并返回其 access token。"""
    await make_user("seed-admin", role="admin")
    return await login("seed-admin")


@pytest.fixture
async def viewer_token(
    make_user: Callable[..., Awaitable[User]], login: Callable[..., Awaitable[str]]
) -> str:
    """创建一个 viewer 用户并返回其 access token。"""
    await make_user("seed-viewer", role="viewer")
    return await login("seed-viewer")
