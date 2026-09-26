"""主题（题材）核心指标口径测试：核心涨幅补齐 + 核心股数回填 + 替换语义。

覆盖三块（全部零网络 / 内存 SQLite）：

1. **provider 补齐**：``xuangutong`` 的 ``theme_rank`` 会另打 ``plate/data`` 把
   ``core_avg_pcp`` 注回榜单条目；补齐失败**不阻断**主采集。
2. **映射口径**：``core_avg_pcp`` 已是小数（0.041 = 4.1%），必须走
   ``ratio_passthrough`` 而非 ``pct_to_ratio``——回归防「被 ÷100」。
3. **仓储/写入器**：``replace_themes`` 先清后写（榜单滚动变化不留残留）、
   空结果不删除；``_replace_theme_stocks`` 回填 ``core_count``。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import date

import httpx
import pytest
from app.datasources.mappings.dsl import apply_mapping
from app.datasources.mappings.registry import get_mapping
from app.datasources.providers import xuangutong as provider_module
from app.datasources.providers.xuangutong import XuangutongProvider
from app.db.base import Base
from app.ingest.writers import _replace_theme_stocks
from app.models.market import Theme, ThemeStock
from app.repositories import Repositories
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

DAY = date(2026, 9, 21)

#: 上游 `surge_stock/plates` 形状（只留测试关心的字段）。
PLATES = {
    "code": 20000,
    "data": {
        "items": [
            {"id": 25638417, "name": "医药", "description": "创新药"},
            {"id": 26346142, "name": "机器人", "description": None},
        ]
    },
}


def _client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[], httpx.AsyncClient]:
    """把 provider 的默认 client 换成离线 MockTransport（provider 负责关闭）。"""

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


# ============================================================ 1. provider 补齐


async def test_theme_rank_fills_core_avg_pcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """榜单条目应被注入 `core_avg_pcp`，且 plate/data 一次批量带上全部 id。"""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/surge_stock/plates"):
            return httpx.Response(200, json=PLATES)
        assert request.url.path.endswith("/plate/data"), request.url
        seen["plates"] = request.url.params["plates"]
        seen["fields"] = request.url.params["fields"]
        return httpx.Response(
            200,
            json={
                "code": 20000,
                "data": {
                    "25638417": {"core_avg_pcp": 0.041, "plate_name": "医药"},
                    "26346142": {"core_avg_pcp": 0.0159, "plate_name": "机器人"},
                },
            },
        )

    monkeypatch.setattr(provider_module, "_make_client", _client_factory(handler))
    provider = XuangutongProvider()
    payload = await provider.fetch("theme_rank", date=DAY)

    items = payload["data"]["items"]
    assert [item["core_avg_pcp"] for item in items] == [0.041, 0.0159]
    # 一次请求批量取，不按题材 N 次打
    assert seen["plates"] == "25638417,26346142"
    assert seen["fields"] == "core_avg_pcp"


async def test_theme_rank_survives_plate_data_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`plate/data` 业务码异常时仍返回榜单（补齐是可选增强，不阻断采集）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/surge_stock/plates"):
            return httpx.Response(200, json=PLATES)
        return httpx.Response(200, json={"code": 50001, "message": "boom"})

    monkeypatch.setattr(provider_module, "_make_client", _client_factory(handler))
    provider = XuangutongProvider()
    payload = await provider.fetch("theme_rank", date=DAY)

    items = payload["data"]["items"]
    assert [item["name"] for item in items] == ["医药", "机器人"]
    assert all("core_avg_pcp" not in item for item in items)


# ============================================================ 2. 映射口径


def test_core_avg_pcp_is_decimal_passthrough() -> None:
    """`core_avg_pcp` 已是小数口径：0.041 必须映射成 0.041（不是 0.00041）。"""
    mapping = get_mapping("xuangutong", "theme_rank")
    rows = apply_mapping(
        mapping,
        {
            "code": 20000,
            "data": {
                "items": [{"id": 1, "name": "医药", "core_avg_pcp": 0.041, "description": "x"}]
            },
        },
        context={"args": {"date": DAY}},
    )

    assert len(rows) == 1
    assert rows[0]["core_avg_pct"] == pytest.approx(0.041)
    assert rows[0]["name"] == "医药"


def test_core_avg_pcp_missing_stays_none() -> None:
    """上游没给核心涨幅时留 None（前端显示 `--`），不填 0。"""
    mapping = get_mapping("xuangutong", "theme_rank")
    rows = apply_mapping(
        mapping,
        {"code": 20000, "data": {"items": [{"id": 1, "name": "医药"}]}},
        context={"args": {"date": DAY}},
    )

    assert rows[0]["core_avg_pct"] is None


# ============================================================ 3. 仓储 / 写入器


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 会话；每例独立建库以保证隔离。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
        yield opened
    await engine.dispose()


def _theme_row(name: str, rank: int) -> dict[str, object]:
    return {"trade_date": DAY, "rank": rank, "name": name, "source": "test"}


def _stock_row(theme_name: str, code: str) -> dict[str, object]:
    return {
        "trade_date": DAY,
        "theme_name": theme_name,
        "code": code,
        "name": code,
        "price": 10.0,
        "pct": 0.05,
        "turnover_rate": 0.03,
        "source": "test",
    }


async def _names(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        select(Theme.name).where(Theme.trade_date == DAY).order_by(Theme.rank)
    )
    return list(rows.scalars().all())


async def test_replace_themes_drops_stale_rows(session: AsyncSession) -> None:
    """榜单滚动变化：第二轮只留下本轮题材，掉出的名字不残留。"""
    repos = Repositories.build(session)
    await repos.themes.replace_themes(DAY, themes=[_theme_row("医药", 1), _theme_row("旧题材", 2)])
    assert await _names(session) == ["医药", "旧题材"]

    await repos.themes.replace_themes(DAY, themes=[_theme_row("医药", 1)])
    assert await _names(session) == ["医药"]


async def test_replace_themes_empty_does_not_delete(session: AsyncSession) -> None:
    """空结果视为「本轮无数据」：不清空已有快照。"""
    repos = Repositories.build(session)
    await repos.themes.replace_themes(DAY, themes=[_theme_row("医药", 1)])

    assert await repos.themes.replace_themes(DAY, themes=[]) == 0
    assert await _names(session) == ["医药"]


async def test_replace_themes_replaces_stocks(session: AsyncSession) -> None:
    """成分股同样是先清后写。"""
    repos = Repositories.build(session)
    await repos.themes.replace_themes(DAY, stocks=[_stock_row("医药", "600001.SH")])
    await repos.themes.replace_themes(DAY, stocks=[_stock_row("医药", "600002.SH")])

    rows = await session.execute(select(ThemeStock.code).where(ThemeStock.trade_date == DAY))
    assert list(rows.scalars().all()) == ["600002.SH"]


async def test_replace_theme_stocks_backfills_core_count() -> None:
    """写入成分股后按题材聚合回填 `themes.core_count`。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repos = Repositories.build(session)
        await repos.themes.replace_themes(
            DAY, themes=[_theme_row("医药", 1), _theme_row("机器人", 2)]
        )
        await session.flush()

        class _Row:
            """最小契约行桩：写入器只读 model_dump()。"""

            def __init__(self, theme_name: str, code: str) -> None:
                self._data = _stock_row(theme_name, code)

            def model_dump(self) -> dict[str, object]:
                return dict(self._data)

        rows = [
            _Row("医药", "600001.SH"),
            _Row("医药", "600002.SH"),
            _Row("医药", "600003.SH"),
            _Row("机器人", "300001.SZ"),
        ]
        written = await _replace_theme_stocks(repos, rows, DAY, "test")  # type: ignore[arg-type]
        assert written == 4

        got = await session.execute(
            select(Theme.name, Theme.core_count).where(Theme.trade_date == DAY).order_by(Theme.rank)
        )
        assert list(got.all()) == [("医药", 3), ("机器人", 1)]

        total = await session.execute(select(func.count()).select_from(ThemeStock))
        assert total.scalar_one() == 4
    await engine.dispose()
