"""auction_grab 插件测试（SQLite 内存库：三腿判定 / 排序截取 / 门控 / 集成）。

种子场景（9 只股票各自隔离一种判定分支，昨收均为 10.00，600600 为 20.00）：

- ``600100`` 正例：p920=9.90 < 昨收、p925=10.20（拉升 3.03%、涨 2.0%）；
- ``600600`` 正例 2（竞价额 2030 万 < 600100 的 5100 万，验证排序）；
- ``600200`` p920=10.10 ≥ 昨收 → 腿2 拒；
- ``600300`` p925=10.05（拉升 1.52%）→ 腿4 拒；
- ``600400`` p925=10.60（对昨收 +6.0%）→ 腿5 拒；
- ``300300`` 创业板正例形态 → 板块过滤拒；
- ``600800`` ST 正例形态 → ST 过滤拒；
- ``600500`` T-1 无日线（昨收缺失）→ 拒；
- ``600700`` 不在 auction_series 池（缺 p920）→ 交集外拒。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from app.core.config import get_settings
from app.db.base import Base
from app.models.market import PoolSnapshot
from app.repositories import Repositories
from app.strategies import (
    CycleState,
    Phase,
    StrategyContext,
    StrategyContextFactory,
    all_strategies,
    clear_registry,
    evaluate_gate,
    override_params,
    register_strategy,
    run_phase,
)
from app.strategies.plugins.auction_grab import AuctionGrabStrategy
from app.strategies.plugins.auction_grab.strategy import _auction_signal
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

PRE_DAY = date(2026, 9, 24)
TRADE_DATE = date(2026, 9, 25)

#: 交易日历池名（与策略常量 _CALENDAR_POOL_NAME 一致）。
_CALENDAR_POOL = "trading_calendar"

#: 全部种子代码。
CODES = (
    "600100",
    "600600",
    "600200",
    "600300",
    "600400",
    "300300",
    "600800",
    "600500",
    "600700",
)


# ============================================================ fixtures


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    """用例内清空注册表保证隔离；结束后还原进入前快照。"""
    snapshot = all_strategies()
    clear_registry()
    yield
    clear_registry()
    for cls in snapshot:
        register_strategy(cls)


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


@pytest.fixture
def repos(session: AsyncSession) -> Repositories:
    """仓储容器。"""
    return Repositories.build(session)


# ============================================================ 种子构造


def _stock_row(code: str) -> dict[str, Any]:
    """股票主档行。"""
    return {
        "code": code,
        "name": f"股票{code}",
        "market": "SH" if code.startswith("6") else "SZ",
        "board": "创业板" if code.startswith("30") else "主板",
        "is_st": code == "600800",
        "list_date": None,
        "source": "test",
    }


def _opening_payload(codes: tuple[str, ...]) -> dict[str, Any]:
    """opening_match 快照 payload：{code: {price, volume_lots, time_label}}。

    数值按模块 docstring 的分支场景取定；600700 有竞价撮合但缺时序。
    """
    values: dict[str, tuple[float, float]] = {
        "600100": (10.20, 50_000.0),
        "600600": (20.30, 10_000.0),
        "600200": (10.15, 30_000.0),
        "600300": (10.05, 30_000.0),
        "600400": (10.60, 30_000.0),
        "300300": (10.20, 50_000.0),
        "600800": (10.20, 50_000.0),
        "600500": (10.20, 50_000.0),
        "600700": (10.20, 50_000.0),
    }
    return {
        code: {"price": price, "volume_lots": vol, "time_label": "09:25"}
        for code, (price, vol) in values.items()
        if code in codes
    }


def _refs_payload(codes: tuple[str, ...]) -> dict[str, Any]:
    """auction_series 快照 payload（writer 已提炼形态）：{code: {p920, time_label}}。"""
    values: dict[str, float] = {
        "600100": 9.90,
        "600600": 19.80,
        "600200": 10.10,
        "600300": 9.90,
        "600400": 9.70,
        "300300": 9.90,
        "600800": 9.90,
        "600500": 9.90,
    }
    return {
        code: {"p920": p920, "time_label": "09:20:03"}
        for code, p920 in values.items()
        if code in codes
    }


async def _seed(
    session: AsyncSession,
    repos: Repositories,
    *,
    codes: tuple[str, ...] = CODES,
    opening: dict[str, Any] | None = None,
    refs: dict[str, Any] | None = None,
    with_calendar: bool = True,
) -> None:
    """种入主档 / 日线（昨收）/ 两个竞价快照池 / 交易日历。"""
    await repos.stocks.upsert_many([_stock_row(code) for code in codes])
    bars = [
        {
            "code": code,
            "trade_date": PRE_DAY,
            "open": Decimal("10.0"),
            "high": Decimal("10.0"),
            "low": Decimal("10.0"),
            "close": Decimal("20.0" if code == "600600" else "10.0"),
            "pre_close": None,
            "volume_shares": 1_000_000,
            "amount_yuan": Decimal("10000000"),
            "source": "test",
        }
        for code in codes
        if code != "600500"  # 600500 缺 T-1 日线：昨收缺失分支
    ]
    await repos.daily_bars.upsert_many(bars)
    snapshots: list[dict[str, Any]] = []
    if with_calendar:
        snapshots.append(
            {
                "trade_date": TRADE_DATE,
                "pool_name": _CALENDAR_POOL,
                "payload": {"dates": [PRE_DAY.isoformat(), TRADE_DATE.isoformat()]},
                "source": "test",
            }
        )
    if opening is not None:
        snapshots.append(
            {
                "trade_date": TRADE_DATE,
                "pool_name": "opening_match",
                "payload": opening,
                "source": "test",
            }
        )
    if refs is not None:
        snapshots.append(
            {
                "trade_date": TRADE_DATE,
                "pool_name": "auction_series",
                "payload": refs,
                "source": "test",
            }
        )
    if snapshots:
        await repos.pool_snapshot.upsert_many(snapshots)
    await session.commit()


def _ctx(repos: Repositories, trade_date: date = TRADE_DATE) -> StrategyContext:
    """手工构造策略上下文（params 留空 → 现场解析默认值）。"""
    return StrategyContext(strategy_id="auction_grab", trade_date=trade_date, repos=repos)


# ============================================================ 门控与声明


def test_gate_matrix_blocks_ice_retreat() -> None:
    """门控：T-1 定版态拦 {冰点, 退潮}；其余五态（含分歧）放行。"""
    for state in CycleState:
        decision = evaluate_gate(AuctionGrabStrategy, state)
        if state in (CycleState.ICE, CycleState.RETREAT):
            assert decision.allowed is False
            assert decision.position_factor == 0.0
        else:
            assert decision.allowed is True
            assert decision.position_factor == 1.0


def test_schema_declaration() -> None:
    """策略声明：单阶段 OPENING / 非空头门 / 5 个参数 / 门控矩阵键。"""
    strategy = AuctionGrabStrategy()
    assert strategy.strategy_id == "auction_grab"
    assert strategy.label == "竞价抢筹"
    assert strategy.phases == frozenset({Phase.OPENING})
    assert strategy.bear_gate is False
    keys = {spec.key for spec in strategy.params_schema}
    assert keys == {"min_rise", "max_chg", "limit", "position", "max_total_position"}
    assert set(strategy.gate_matrix) == set(CycleState)
    for state, rule in strategy.gate_matrix.items():
        if state in (CycleState.ICE, CycleState.RETREAT):
            assert rule.allowed is False
            assert rule.position_factor == 0.0
        else:
            assert rule.allowed is True
            assert rule.position_factor == 1.0


# ============================================================ 三腿纯函数


def test_auction_signal_cases() -> None:
    """三腿布尔式逐场景：正例 / 腿2 拒 / 腿4 拒 / 腿5 拒 / 非正价拒。"""
    kwargs: dict[str, Any] = {"min_rise": 0.02, "max_chg": 0.05}

    legs = _auction_signal(p920=9.90, p925=10.20, pre_close=10.0, **kwargs)
    assert legs is not None
    assert legs["rise_vs_p920"] == pytest.approx(10.20 / 9.90 - 1)
    assert legs["chg_vs_pc"] == pytest.approx(0.02)

    # 腿2：p920 >= 昨收（非低吸位）
    assert _auction_signal(p920=10.0, p925=10.20, pre_close=10.0, **kwargs) is None
    # 腿4：拉升不足
    assert _auction_signal(p920=9.90, p925=10.05, pre_close=10.0, **kwargs) is None
    # 腿5：对昨收过热
    assert _auction_signal(p920=9.70, p925=10.60, pre_close=10.0, **kwargs) is None
    # 非正价：脏数据守卫
    assert _auction_signal(p920=0.0, p925=10.20, pre_close=10.0, **kwargs) is None


# ============================================================ OPENING 竞价确认


async def test_confirm_opening_emits_and_persists(
    session: AsyncSession, repos: Repositories
) -> None:
    """三腿正例 → 产出提醒并落库；卡片键集合与 dragon 完全一致。"""
    await _seed(
        session,
        repos,
        opening=_opening_payload(CODES),
        refs=_refs_payload(CODES),
    )
    output = await AuctionGrabStrategy().confirm_opening(_ctx(repos))

    # 仅两只正例；600100 竞价额（5100 万）> 600600（2030 万）→ 排序在前
    advices = output["advices"]
    assert [item["code"] for item in advices] == ["600100", "600600"]
    advice = advices[0]
    assert set(advice) == {
        "path_id",
        "path_label",
        "code",
        "name",
        "buy_day",
        "buy_price",
        "gates",
        "bonus",
        "bonus_score",
        "position",
        "stop_loss_price",
        "sell_timing",
        "sell_price_ref",
        "field_snapshot",
    }
    assert advice["buy_day"] == TRADE_DATE.isoformat()
    assert advice["buy_price"] == pytest.approx(10.20)
    assert advice["position"] == pytest.approx(0.20)
    assert advice["stop_loss_price"] is None
    assert advice["path_id"] == "auction"
    assert advice["sell_timing"].startswith("T+1 开盘卖出")
    assert advice["field_snapshot"]["p920"] == pytest.approx(9.90)
    assert advice["field_snapshot"]["chg_vs_pc"] == pytest.approx(0.02)
    assert advice["field_snapshot"]["matched_amount_yuan"] == pytest.approx(10.20 * 5e6)
    assert {gate["factor_id"] for gate in advice["gates"]} == {
        "p920_lt_pc",
        "rise_vs_p920",
        "chg_vs_pc",
        "amt_rank",
    }

    rows = await repos.advice_reports.get_by_date(TRADE_DATE, kind="advice")
    assert [row.code for row in rows] == ["600100", "600600"]


async def test_confirm_opening_filters(session: AsyncSession, repos: Repositories) -> None:
    """逐分支过滤：腿2 / 腿4 / 腿5 / 创业板 / ST / 缺昨收 / 缺 p920 全拒。"""
    await _seed(
        session,
        repos,
        codes=("600200", "600300", "600400", "300300", "600800", "600500", "600700"),
        opening=_opening_payload(
            ("600200", "600300", "600400", "300300", "600800", "600500", "600700")
        ),
        refs=_refs_payload(("600200", "600300", "600400", "300300", "600800", "600500")),
    )
    output = await AuctionGrabStrategy().confirm_opening(_ctx(repos))
    assert output["advices"] == []
    rows = await repos.advice_reports.get_by_date(TRADE_DATE, kind="advice")
    assert rows == []


async def test_confirm_opening_rejects_exrights_artifact(
    session: AsyncSession, repos: Repositories
) -> None:
    """回归（除权护栏）：竞价价与昨收复权口径未对齐（≈ -49%）整票拒收。

    伪跳变票三腿原本全过（腿2 4.95<10、腿4 +3.0%、腿5 -49%<max_chg），
    单边阈值放行买入；护栏必须拦下，且不影响其余正例。
    """
    await _seed(
        session,
        repos,
        codes=(*CODES, "600105"),
        opening={
            **_opening_payload(CODES),
            "600105": {"price": 5.10, "volume_lots": 50_000.0, "time_label": "09:25"},
        },
        refs={**_refs_payload(CODES), "600105": {"p920": 4.95, "time_label": "09:20:03"}},
    )
    output = await AuctionGrabStrategy().confirm_opening(_ctx(repos))

    assert [item["code"] for item in output["advices"]] == ["600100", "600600"]
    rows = await repos.advice_reports.get_by_date(TRADE_DATE, kind="advice")
    assert [row.code for row in rows] == ["600100", "600600"]


async def test_confirm_opening_rank_limit(session: AsyncSession, repos: Repositories) -> None:
    """min(limit, n) 截取：4 只正例按竞价额降序只取前 3。"""
    codes = ("600101", "600102", "600103", "600104")
    await repos.stocks.upsert_many([_stock_row(code) for code in codes])
    await repos.daily_bars.upsert_many(
        [
            {
                "code": code,
                "trade_date": PRE_DAY,
                "open": Decimal("10.0"),
                "high": Decimal("10.0"),
                "low": Decimal("10.0"),
                "close": Decimal("10.0"),
                "pre_close": None,
                "volume_shares": 1_000_000,
                "amount_yuan": Decimal("10000000"),
                "source": "test",
            }
            for code in codes
        ]
    )
    await repos.pool_snapshot.upsert_many(
        [
            {
                "trade_date": TRADE_DATE,
                "pool_name": _CALENDAR_POOL,
                "payload": {"dates": [PRE_DAY.isoformat(), TRADE_DATE.isoformat()]},
                "source": "test",
            },
            {
                "trade_date": TRADE_DATE,
                "pool_name": "opening_match",
                # 竞价额：101 > 102 > 103 > 104（10.3×3e6 > 10.2×2e6 > 10.1×2e6 > 10.3×1e6）
                "payload": {
                    "600101": {"price": 10.30, "volume_lots": 30_000.0, "time_label": "09:25"},
                    "600102": {"price": 10.20, "volume_lots": 20_000.0, "time_label": "09:25"},
                    "600103": {"price": 10.10, "volume_lots": 20_000.0, "time_label": "09:25"},
                    "600104": {"price": 10.30, "volume_lots": 10_000.0, "time_label": "09:25"},
                },
                "source": "test",
            },
            {
                "trade_date": TRADE_DATE,
                "pool_name": "auction_series",
                "payload": {
                    **{code: {"p920": 9.90, "time_label": "09:20:03"} for code in codes},
                    "600104": {"p920": 9.80, "time_label": "09:20:03"},
                },
                "source": "test",
            },
        ]
    )
    await session.commit()

    output = await AuctionGrabStrategy().confirm_opening(_ctx(repos))
    assert [item["code"] for item in output["advices"]] == ["600101", "600102", "600103"]
    ranks = {
        item["code"]: next(
            gate["detail"] for gate in item["gates"] if gate["factor_id"] == "amt_rank"
        )
        for item in output["advices"]
    }
    assert "前 1" in ranks["600101"]

    # limit 参数放宽到 4：全部通过（override_params 走 contextvars 生效）
    with override_params("auction_grab", {"limit": 4}):
        output = await AuctionGrabStrategy().confirm_opening(_ctx(repos))
    assert [item["code"] for item in output["advices"]] == list(codes)


async def test_confirm_opening_missing_inputs(session: AsyncSession, repos: Repositories) -> None:
    """缺竞价撮合 / 缺时序 / 缺交易日历 → 空提醒早退（不臆断）。"""
    ctx = _ctx(repos)
    strategy = AuctionGrabStrategy()

    # 无 opening_match 快照
    await _seed(session, repos, refs=_refs_payload(CODES))
    assert (await strategy.confirm_opening(ctx))["advices"] == []

    # 无 auction_series 快照
    await _seed(session, repos, opening=_opening_payload(CODES))
    await session.execute(delete(PoolSnapshot).where(PoolSnapshot.pool_name == "auction_series"))
    await session.commit()
    assert (await strategy.confirm_opening(ctx))["advices"] == []

    # 无交易日历（无法确定 T-1 昨收日）
    await _seed(
        session,
        repos,
        opening=_opening_payload(CODES),
        refs=_refs_payload(CODES),
        with_calendar=False,
    )
    await session.execute(delete(PoolSnapshot).where(PoolSnapshot.pool_name == _CALENDAR_POOL))
    await session.commit()
    assert (await strategy.confirm_opening(ctx))["advices"] == []


# ============================================================ run_phase 集成


async def test_run_phase_opening_integration(session: AsyncSession, repos: Repositories) -> None:
    """注册 → run_phase(OPENING) → 提醒产出；UNKNOWN 态显式放行不回退。"""
    await _seed(
        session,
        repos,
        codes=("600100",),
        opening=_opening_payload(("600100",)),
        refs=_refs_payload(("600100",)),
    )
    register_strategy(AuctionGrabStrategy)
    factory = StrategyContextFactory(repos=repos, settings=get_settings(), trade_date=TRADE_DATE)

    summary = await run_phase(Phase.OPENING, factory, repos)

    assert summary.failure_count == 0
    assert "auction_grab" in summary.succeeded_ids
    result = next(item for item in summary.results if item.strategy_id == "auction_grab")
    assert result.ok is True
    assert len(result.output["advices"]) == 1
    assert result.gate is not None
    assert result.gate.allowed is True
    assert result.gate.position_factor == 1.0
