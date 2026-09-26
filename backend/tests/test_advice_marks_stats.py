"""T-0022 补测：建议「已买入」标记、跨日分策略战绩、本金自助更新。

覆盖 T-0022 新增的三块面（此前仅由既有 _stats/_evaluate 测试间接覆盖）：

- ``GET/POST /api/advice/marks``：标记 / 幂等 / 取消删行 / 审计留痕；
- ``ReviewService.strategy_stats``：跨日按策略分组、同键去重保留最新、
  pending 单列、自然日窗口（30/90/0）过滤与评估口径（与单日回溯一致，
  auction 走可卖日开盘价）；
- ``PATCH /api/auth/me``：本金写入 / 清除（全量替换语义）/ 参数校验。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from app.models.auth import AuditLog
from app.repositories import Repositories
from app.services.review_service import ReviewService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import STOCK_CODE, TRADE_DATE, auth_header, seed_market


@pytest.fixture
async def stats_seeded(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """行情种子 + 跨日多策略建议（评估口径沿用 test_review.py）。

    dragon：closed（01-08 买 11.6，01-09 收盘 12.0 了结）+ 同键旧重复 +
    stopped（止损 11.8）+ pending（06-05 买在最后一根日线）+ 一条 trade_date
    在 30 日窗口外（05-01 < start 05-04）、90 日窗口内（> 03-05）的旧行；
    auction 独立 strategy_id 验证分策略分组，sell_price_ref="open"，
    01-09 开盘 11.9 了结。
    closed 用 15:06 与 seed 自带的 dragon 无买点日建议（15:05）错开——
    upsert 键含 ran_at，同刻会互相覆盖；seed 条因此存活为独立 pending（所有窗口均含）。
    """
    await seed_market(session_factory)
    async with session_factory() as session:
        repos = Repositories.build(session)
        base = {
            "trade_date": TRADE_DATE,
            "kind": "advice",
            "strategy_id": "dragon",
            "strategy_version": None,
            "code": STOCK_CODE,
        }
        closed = {
            **base,
            "ran_at": datetime(2026, 6, 3, 15, 6, tzinfo=UTC),
            "payload": {
                "path_id": "S2",
                "code": STOCK_CODE,
                "name": "测试一号",
                "buy_day": "2026-01-08",
                "buy_price": 11.6,
                "position": 0.2,
                "stop_loss_price": 11.252,
            },
        }
        # 同键旧一次运行（去重应保留最新 closed）
        duplicate = {**closed, "ran_at": datetime(2026, 6, 3, 15, 0, tzinfo=UTC)}
        stopped = {
            **base,
            "ran_at": datetime(2026, 6, 3, 15, 4, tzinfo=UTC),
            "payload": {
                "path_id": "S4",
                "code": STOCK_CODE,
                "name": "测试一号",
                "buy_day": "2026-01-08",
                "buy_price": 11.6,
                "position": 0.25,
                "stop_loss_price": 11.8,
            },
        }
        pending = {
            **base,
            "ran_at": datetime(2026, 6, 3, 15, 3, tzinfo=UTC),
            "payload": {
                "path_id": "S2",
                "code": STOCK_CODE,
                "name": "测试一号",
                "buy_day": "2026-06-05",
                "buy_price": 14.6,
                "stop_loss_price": 14.16,
            },
        }
        auction = {
            **base,
            "strategy_id": "auction",
            "ran_at": datetime(2026, 6, 3, 15, 2, tzinfo=UTC),
            "payload": {
                "path_id": "auction",
                "code": STOCK_CODE,
                "name": "测试一号",
                "buy_day": "2026-01-08",
                "buy_price": 11.6,
                "position": 0.15,
                "stop_loss_price": None,
                "sell_price_ref": "open",
            },
        }
        # 窗口外旧行：30 日窗不含、90 日窗/全部历史含（03-06 可卖收盘 14.6）
        older = {
            **base,
            "trade_date": date(2026, 5, 1),
            "ran_at": datetime(2026, 5, 1, 15, 5, tzinfo=UTC),
            "payload": {
                "path_id": "S4",
                "code": STOCK_CODE,
                "name": "测试一号",
                "buy_day": "2026-03-05",
                "buy_price": 11.6,
                "stop_loss_price": None,
            },
        }
        await repos.advice_reports.upsert_many(
            [closed, duplicate, stopped, pending, auction, older]
        )
        await session.commit()
    yield session_factory


async def _strategy_stats(
    factory: async_sessionmaker[AsyncSession], days: int | None
):
    async with factory() as session:
        service = ReviewService(Repositories.build(session))
        return await service.strategy_stats(days)


# ============================================================ 策略战绩聚合


async def test_strategy_stats_empty(session_factory) -> None:
    """无建议报告：空列表 + 窗口回显。"""
    result = await _strategy_stats(session_factory, 30)
    assert result.items == []
    assert result.days == 30
    assert result.start_date is None and result.end_date is None


async def test_strategy_stats_group_and_dedupe(stats_seeded) -> None:
    """默认 30 日窗：按策略分组、同键去重保留最新、pending 单列。"""
    result = await _strategy_stats(stats_seeded, 30)

    assert result.days == 30
    assert result.start_date == date(2026, 5, 4)
    assert result.end_date == TRADE_DATE
    # 按 strategy_id 排序输出
    assert [item.strategy_id for item in result.items] == ["auction", "dragon"]

    r_closed = 12.0 / 11.6 - 1
    r_stopped = 11.8 / 11.6 - 1
    r_auction = 11.9 / 11.6 - 1

    by = {item.strategy_id: item for item in result.items}
    auction = by["auction"]
    assert (auction.total, auction.settled, auction.pending) == (1, 1, 0)
    assert auction.win_count == 1
    assert auction.win_rate == pytest.approx(1.0)
    assert auction.avg_return_pct == pytest.approx(r_auction)

    dragon = by["dragon"]
    # closed + stopped + pending + seed 无买点日条（旧 duplicate 去重不计）
    assert (dragon.total, dragon.settled, dragon.pending) == (4, 2, 2)
    assert dragon.win_count == 2
    assert dragon.win_rate == pytest.approx(1.0)
    assert dragon.avg_return_pct == pytest.approx((r_closed + r_stopped) / 2)


async def test_strategy_stats_window(stats_seeded) -> None:
    """90 日窗纳入窗口外旧行；0 = 全部历史（start_date 为空）。"""
    r_closed = 12.0 / 11.6 - 1
    r_stopped = 11.8 / 11.6 - 1
    r_older = 14.6 / 11.6 - 1  # 03-06 可卖日收盘 14.6（03-05 收盘 14.3）

    result = await _strategy_stats(stats_seeded, 90)
    assert result.start_date == date(2026, 3, 5)
    dragon = next(item for item in result.items if item.strategy_id == "dragon")
    assert (dragon.total, dragon.settled, dragon.pending) == (5, 3, 2)
    assert dragon.avg_return_pct == pytest.approx((r_closed + r_stopped + r_older) / 3)

    full = await _strategy_stats(stats_seeded, 0)
    assert full.start_date is None
    full_dragon = next(item for item in full.items if item.strategy_id == "dragon")
    assert full_dragon.total == 5


async def test_strategy_stats_api(app, client, viewer_token, stats_seeded) -> None:
    """API：viewer 可读；days 超上限 422；未登录 401。"""
    headers = auth_header(viewer_token)
    response = await client.get(
        "/api/review/strategy-stats", params={"days": 0}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days"] == 0 and body["start_date"] is None
    assert [item["strategy_id"] for item in body["items"]] == ["auction", "dragon"]

    invalid = await client.get(
        "/api/review/strategy-stats", params={"days": 731}, headers=headers
    )
    assert invalid.status_code == 422

    anonymous = await client.get("/api/review/strategy-stats")
    assert anonymous.status_code == 401


# ============================================================ 已买入标记


async def test_marks_requires_auth(client) -> None:
    """未登录读/写均拒绝。"""
    day = TRADE_DATE.isoformat()
    get_anonymous = await client.get("/api/advice/marks", params={"date": day})
    assert get_anonymous.status_code == 401
    post_anonymous = await client.post(
        "/api/advice/marks",
        json={
            "trade_date": day,
            "strategy_id": "dragon",
            "code": STOCK_CODE,
            "bought": True,
        },
    )
    assert post_anonymous.status_code == 401


async def test_marks_roundtrip_and_audit(
    app, client, viewer_token, session_factory
) -> None:
    """标记 → 幂等重复 → 多票 → 取消删行；GET 与 POST 全量列表一致；审计逐次留痕。"""
    headers = auth_header(viewer_token)
    day = TRADE_DATE.isoformat()

    empty = await client.get("/api/advice/marks", params={"date": day}, headers=headers)
    assert empty.status_code == 200 and empty.json()["items"] == []

    # 标记第一票
    first = await client.post(
        "/api/advice/marks",
        json={
            "trade_date": day,
            "strategy_id": "dragon",
            "code": STOCK_CODE,
            "bought": True,
        },
        headers=headers,
    )
    assert first.status_code == 200, first.text
    items = first.json()["items"]
    assert len(items) == 1
    assert items[0]["trade_date"] == day
    assert items[0]["strategy_id"] == "dragon"
    assert items[0]["code"] == STOCK_CODE
    assert items[0]["marked_by"] == "seed-viewer"
    assert items[0]["marked_at"] is not None

    # 幂等重复标记：仍一条（upsert 覆盖）
    again = await client.post(
        "/api/advice/marks",
        json={
            "trade_date": day,
            "strategy_id": "dragon",
            "code": STOCK_CODE,
            "bought": True,
        },
        headers=headers,
    )
    assert len(again.json()["items"]) == 1

    # 第二票（另一策略）
    second = await client.post(
        "/api/advice/marks",
        json={
            "trade_date": day,
            "strategy_id": "lianban",
            "code": "600002",
            "bought": True,
        },
        headers=headers,
    )
    assert {(i["strategy_id"], i["code"]) for i in second.json()["items"]} == {
        ("dragon", STOCK_CODE),
        ("lianban", "600002"),
    }

    # 取消第一票：删行，列表只剩第二票
    cancel = await client.post(
        "/api/advice/marks",
        json={
            "trade_date": day,
            "strategy_id": "dragon",
            "code": STOCK_CODE,
            "bought": False,
        },
        headers=headers,
    )
    remaining = [(i["strategy_id"], i["code"]) for i in cancel.json()["items"]]
    assert remaining == [("lianban", "600002")]

    # GET 与 POST 返回的全量列表一致
    got = await client.get("/api/advice/marks", params={"date": day}, headers=headers)
    assert [(i["strategy_id"], i["code"]) for i in got.json()["items"]] == remaining

    # 审计：4 次写各留一条 advice_mark，含操作者 / 目标 / 明细
    async with session_factory() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "advice_mark")))
            .scalars()
            .all()
        )
    assert len(rows) == 4
    cancel_row = next(row for row in rows if row.detail and row.detail.get("bought") is False)
    assert cancel_row.actor == "seed-viewer"
    assert cancel_row.target == f"{day}:dragon:{STOCK_CODE}"


# ============================================================ 本金自助更新


async def test_update_capital_requires_auth(client) -> None:
    """未登录拒绝。"""
    response = await client.patch("/api/auth/me", json={"capital_yuan": 10000})
    assert response.status_code == 401


async def test_update_capital_roundtrip(client, viewer_token) -> None:
    """写入 → 持久化 → 清除（全量替换语义）；负数 422。"""
    headers = auth_header(viewer_token)

    before = await client.get("/api/auth/me", headers=headers)
    assert before.status_code == 200
    assert before.json()["user"]["capital_yuan"] is None

    updated = await client.patch(
        "/api/auth/me", json={"capital_yuan": 100000.5}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["capital_yuan"] == pytest.approx(100000.5)

    persisted = await client.get("/api/auth/me", headers=headers)
    assert persisted.json()["user"]["capital_yuan"] == pytest.approx(100000.5)

    cleared = await client.patch(
        "/api/auth/me", json={"capital_yuan": None}, headers=headers
    )
    assert cleared.status_code == 200
    assert cleared.json()["capital_yuan"] is None

    invalid = await client.patch("/api/auth/me", json={"capital_yuan": -1}, headers=headers)
    assert invalid.status_code == 422
