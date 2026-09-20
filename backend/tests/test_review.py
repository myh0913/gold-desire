"""复盘域测试：建议回溯口径（closed / stopped / pending / 去重）、聚合与 API 权限。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories import Repositories
from app.services.review_service import ReviewService
from tests.conftest import STOCK_CODE, TRADE_DATE, seed_market


@pytest.fixture
async def seeded(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """行情种子 + 三条可评估建议（closed / stopped / pending）+ 一条旧重复。"""
    await seed_market(session_factory)
    async with session_factory() as session:
        repos = Repositories.build(session)
        base = {
            "trade_date": TRADE_DATE,
            "kind": "advice",
            "strategy_id": "dragon",
            "strategy_version": None,
        }
        # closed：买 11.6（01-08 开盘），可卖日 01-09 low=11.7 > 止损 11.252 → 收盘 12.0 了结
        closed = {
            **base,
            "ran_at": datetime(2026, 6, 3, 15, 5, tzinfo=UTC),
            "payload": {
                "path_id": "S2",
                "path_label": "高位跳水型",
                "code": STOCK_CODE,
                "name": "测试一号",
                "buy_day": "2026-01-08",
                "buy_price": 11.6,
                "position": 0.2,
                "stop_loss_price": 11.252,
                "sell_timing": "T1 收盘了结",
                "bonus_score": 1,
            },
        }
        # 同票同路的旧一次运行（去重应保留最新 closed）
        duplicate = {**closed, "ran_at": datetime(2026, 6, 3, 15, 0, tzinfo=UTC)}
        # stopped：止损 11.8 ≥ 可卖日 low 11.7 → 触发止损
        stopped = {
            **base,
            "ran_at": datetime(2026, 6, 3, 15, 4, tzinfo=UTC),
            "payload": {
                "path_id": "S4",
                "path_label": "缩量反转型",
                "code": STOCK_CODE,
                "name": "测试一号",
                "buy_day": "2026-01-08",
                "buy_price": 11.6,
                "position": 0.25,
                "stop_loss_price": 11.8,
            },
        }
        # pending：买在最后一根日线（06-05），其后无日线
        pending = {
            **base,
            "ran_at": datetime(2026, 6, 3, 15, 3, tzinfo=UTC),
            "payload": {
                "path_id": "S2",
                "path_label": "高位跳水型",
                "code": STOCK_CODE,
                "name": "测试一号",
                "buy_day": "2026-06-05",
                "buy_price": 14.6,
                "stop_loss_price": 14.16,
            },
        }
        await repos.advice_reports.upsert_many([closed, duplicate, stopped, pending])
        await session.commit()
    yield session_factory


async def _review(factory, on_date: date | None = TRADE_DATE):
    async with factory() as session:
        service = ReviewService(Repositories.build(session))
        return await service.review(on_date)


# ============================================================ 服务层


async def test_advice_outcomes_and_dedupe(seeded) -> None:
    """closed/stopped/pending 三态口径正确；同票同路去重保留最新一次。"""
    review = await _review(seeded)

    by_key = {(a.path_id, str(a.buy_day)): a for a in review.advices}
    closed = by_key[("S2", "2026-01-08")]
    assert closed.status == "closed"
    assert closed.sell_date == date(2026, 1, 9)
    assert closed.sell_price == pytest.approx(12.0)
    assert closed.return_pct == pytest.approx(12.0 / 11.6 - 1)

    stopped = by_key[("S4", "2026-01-08")]
    assert stopped.status == "stopped"
    assert stopped.return_pct == pytest.approx(11.8 / 11.6 - 1)

    pending = by_key[("S2", "2026-06-05")]
    assert pending.status == "pending"
    # 去重：同键（code+path+买点日）的旧一次运行不重复出现
    assert len([a for a in review.advices if a.path_id == "S2" and a.buy_day == date(2026, 1, 8)]) == 1


async def test_stats_sentiment_pools(seeded) -> None:
    """汇总统计 / 情绪环比 / 池型计数与天梯头部。"""
    review = await _review(seeded)

    stats = review.advice_stats
    assert stats.total == 3  # closed + stopped + pending（seed 那条与 closed 同 ran_at 被 upsert 覆盖）
    assert stats.settled == 2
    assert stats.pending == 1
    assert stats.win_count == 2  # closed +3.45% / stopped +1.72% 均为正
    assert stats.win_rate == pytest.approx(1.0)
    assert stats.avg_return_pct == pytest.approx((12.0 / 11.6 - 1 + 11.8 / 11.6 - 1) / 2)

    assert review.sentiment is not None
    assert review.sentiment.temperature == pytest.approx(62.5)
    assert review.prev_temperature == pytest.approx(58.0)
    assert review.temperature_delta == pytest.approx(4.5)

    assert review.pool_counts == {"limit_up": 2, "broken": 1}
    assert review.top_ladder[0].code == STOCK_CODE
    assert review.top_ladder[0].continue_days == 3


async def test_review_dates(seeded) -> None:
    """可复盘日期来自情绪行（倒序）。"""
    async with seeded() as session:
        service = ReviewService(Repositories.build(session))
        result = await service.review_dates(None)
    assert result.dates[0] == TRADE_DATE
    assert date(2026, 6, 2) in result.dates


# ============================================================ API 层


async def test_review_api_requires_auth(client) -> None:
    """未登录拒绝。"""
    response = await client.get("/api/review")
    assert response.status_code == 401


async def test_review_api_roundtrip(
    app, client, viewer_token, market_seed
) -> None:
    """登录用户可取复盘聚合与日期列表。"""
    response = await client.get(
        "/api/review", params={"date": TRADE_DATE.isoformat()},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trade_date"] == TRADE_DATE.isoformat()
    assert body["pool_counts"]["limit_up"] == 2

    dates = await client.get(
        "/api/review/dates",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert dates.status_code == 200
    assert TRADE_DATE.isoformat() in dates.json()["dates"]
