"""firstboard_dip 插件测试（SQLite 内存库：判定链 / 扫描 / 提醒 / 门控 / 集成）。

种子场景（8 只股票各自隔离一种判定分支）：

- ``600001`` 正例：60 根平盘 + 首板（窗口位置分位 0.5）；
- ``300001`` 创业板 → 板块过滤拒；
- ``600002`` ST → ST 过滤拒；
- ``600003`` 连板（末两日连续涨停）→ 非首板拒；
- ``600004`` 高位首板（位置分位 1.0）→ 分位上限拒；
- ``600005`` 板前 10 日内有板 → 无板窗口拒；
- ``600006`` 历史不足 61 根 → 窗口不足拒；
- ``600007`` 板日无数据 → 扫描层守卫拒。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta
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
    StrategyContextFactory,
    all_strategies,
    clear_registry,
    evaluate_gate,
    override_params,
    register_strategy,
    run_phase,
)
from app.strategies.context import StrategyContext
from app.strategies.plugins.firstboard_dip import FirstboardDipStrategy
from app.strategies.plugins.firstboard_dip.signals import (
    _backfill_pre_close,
    _Bar,
    _firstboard_signal,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BOARD_DAY = date(2026, 9, 17)
TRADE_DATE = date(2026, 9, 18)

#: 交易日历池名（与策略常量 _CALENDAR_POOL_NAME 一致）。
_CALENDAR_POOL = "trading_calendar"

#: 参与种子扫描的全部股票代码（每只对应一种判定分支）。
CODES = ("600001", "300001", "600002", "600003", "600004", "600005", "600006", "600007")


# ============================================================ fixtures


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    """用例内清空注册表保证隔离；结束后还原进入前快照。

    不能只清空：``app.strategies`` 包在 import 时对 plugins/ 做过一次发现，
    清空后不会有第二次自动发现，裸 clear 会把后续测试（如需要 dragon 注册的
    调度链路测试）的注册表一并掏空。
    """
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


def _bar(code: str, day: date, o: float, h: float, low: float, c: float) -> dict[str, Any]:
    """构造一行日线（pre_close 恒为 None，实测插件的回填链路）。"""
    return {
        "code": code,
        "trade_date": day,
        "open": Decimal(str(o)),
        "high": Decimal(str(h)),
        "low": Decimal(str(low)),
        "close": Decimal(str(c)),
        "pre_close": None,
        "volume_shares": 1_000_000,
        "amount_yuan": Decimal("10000000"),
        "source": "test",
    }


def _days(last_day: date, total: int) -> list[date]:
    """以 last_day 结尾的 total 个自然日（升序；日线表不校验交易日历）。"""
    return [last_day - timedelta(days=total - 1 - i) for i in range(total)]


def _flat_rows(code: str, days: list[date]) -> list[dict[str, Any]]:
    """全平日线：开高低收均为 10.0（涨跌幅 0%，无板）。"""
    return [_bar(code, day, 10.0, 10.0, 10.0, 10.0) for day in days]


def _default_bars(code: str, last_day: date) -> list[dict[str, Any]]:
    """默认 61 根：60 根平盘 + 末根首板形态（10/12/10/11，涨停 + 中位）。"""
    days = _days(last_day, 61)
    rows = _flat_rows(code, days[:-1])
    rows.append(_bar(code, days[-1], 10.0, 12.0, 10.0, 11.0))
    return rows


def _case_bars(code: str) -> list[dict[str, Any]]:
    """按代码构造各自的判定场景（见模块 docstring）。"""
    if code in ("600001", "300001", "600002"):
        return _default_bars(code, BOARD_DAY)
    if code == "600003":  # 连板：末两日连续涨停
        days = _days(BOARD_DAY, 61)
        rows = _flat_rows(code, days[:-2])
        rows.append(_bar(code, days[-2], 10.0, 11.0, 10.0, 11.0))
        rows.append(_bar(code, days[-1], 11.0, 12.1, 11.0, 12.1))
        return rows
    if code == "600004":  # 高位首板：窗口 [10, 11] → 位置分位 1.0
        days = _days(BOARD_DAY, 61)
        rows = _flat_rows(code, days[:-1])
        rows.append(_bar(code, days[-1], 10.0, 11.0, 10.0, 11.0))
        return rows
    if code == "600005":  # 板前 10 日内有板（索引 55）→ 无板窗口拒
        days = _days(BOARD_DAY, 61)
        rows = _flat_rows(code, days)
        rows[55] = _bar(code, days[55], 10.0, 11.0, 10.0, 11.0)
        rows[60] = _bar(code, days[60], 10.0, 12.0, 10.0, 11.0)
        return rows
    if code == "600006":  # 历史不足 61 根（仅 31 根）
        days = _days(BOARD_DAY, 31)
        rows = _flat_rows(code, days[:-1])
        rows.append(_bar(code, days[-1], 10.0, 12.0, 10.0, 11.0))
        return rows
    if code == "600007":  # 板日无数据：61 根止于 BOARD_DAY-1
        return _default_bars(code, BOARD_DAY - timedelta(days=1))
    raise AssertionError(code)


async def _seed(
    session: AsyncSession,
    repos: Repositories,
    *,
    opening_prices: dict[str, float] | None = None,
    with_calendar: bool = True,
) -> None:
    """种入股票主档、日线、快照池（交易日历 / 可选竞价撮合）。"""
    await repos.stocks.upsert_many(
        [
            {
                "code": code,
                "name": f"股票{code}",
                "market": "SH",
                "board": "创业板" if code == "300001" else "主板",
                "is_st": code == "600002",
                "list_date": None,
                "source": "test",
            }
            for code in CODES
        ]
    )
    bars: list[dict[str, Any]] = []
    for code in CODES:
        bars.extend(_case_bars(code))
    await repos.daily_bars.upsert_many(bars)
    snapshots: list[dict[str, Any]] = []
    if with_calendar:
        snapshots.append(
            {
                "trade_date": TRADE_DATE,
                "pool_name": _CALENDAR_POOL,
                "payload": {"dates": [BOARD_DAY.isoformat(), TRADE_DATE.isoformat()]},
                "source": "test",
            }
        )
    if opening_prices is not None:
        snapshots.append(
            {
                "trade_date": TRADE_DATE,
                "pool_name": "opening_match",
                "payload": {
                    code: {"price": price, "volume_lots": 100, "time_label": "09:25"}
                    for code, price in opening_prices.items()
                },
                "source": "test",
            }
        )
    if snapshots:
        await repos.pool_snapshot.upsert_many(snapshots)
    await session.commit()


def _to_bars(rows: list[dict[str, Any]]) -> list[_Bar]:
    """种子行转纯函数输入（pre_close 置 None，走回填链路）。"""
    return [
        _Bar(
            trade_date=row["trade_date"],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            pre_close=None,
        )
        for row in rows
    ]


def _ctx(repos: Repositories, trade_date: date = TRADE_DATE) -> StrategyContext:
    """手工构造策略上下文（params 留空 → 现场解析，override_params 生效）。"""
    return StrategyContext(strategy_id="firstboard_dip", trade_date=trade_date, repos=repos)


# ============================================================ 门控与声明


def test_gate_matrix_allows_all_states() -> None:
    """全七周期态显式放行：低吸逆势路径不做门控拦截。"""
    for state in CycleState:
        decision = evaluate_gate(FirstboardDipStrategy, state)
        assert decision.allowed is True
        assert decision.position_factor == 1.0


def test_schema_declaration() -> None:
    """策略声明：双阶段 / 非空头门 / 8 个参数 / 七态矩阵。"""
    strategy = FirstboardDipStrategy()
    assert strategy.strategy_id == "firstboard_dip"
    assert strategy.label == "首板低吸"
    assert Phase.POOL in strategy.phases
    assert Phase.OPENING in strategy.phases
    assert strategy.bear_gate is False
    keys = {spec.key for spec in strategy.params_schema}
    assert keys == {
        "dip_low",
        "dip_high",
        "pos_max",
        "limit_up_pct",
        "no_board_days",
        "pos_window",
        "position",
        "max_total_position",
    }
    assert set(strategy.gate_matrix) == set(CycleState)
    for rule in strategy.gate_matrix.values():
        assert rule.allowed is True
        assert rule.position_factor == 1.0


# ============================================================ 判定链纯函数


def test_firstboard_signal_cases() -> None:
    """判定链逐场景：正例 / 连板 / 高位 / 板前有板 / 历史不足 / 脏数据护栏。"""
    kwargs: dict[str, Any] = {
        "limit_up_pct": 0.098,
        "no_board_days": 10,
        "pos_window": 60,
        "pos_max": 0.55,
    }

    def judge(code: str) -> dict[str, Any] | None:
        return _firstboard_signal(_backfill_pre_close(_to_bars(_case_bars(code))), **kwargs)

    signal = judge("600001")
    assert signal is not None
    assert signal["first_board_date"] == BOARD_DAY
    assert signal["board_close"] == pytest.approx(11.0)
    assert signal["pos_60d"] == pytest.approx(0.5)

    assert judge("600003") is None  # 连板非首板
    assert judge("600004") is None  # 位置分位 1.0 超上限
    assert judge("600005") is None  # 板前 10 日内有板
    assert judge("600006") is None  # 历史不足 61 根

    # 脏数据护栏：末根涨幅 11% 超过 _SUSPECT_PCT（疑似除权未回填）→ 放弃
    suspect_rows = _default_bars("600001", BOARD_DAY)
    suspect_rows[-1] = _bar("600001", BOARD_DAY, 10.0, 12.1, 10.0, 11.1)
    bars = _backfill_pre_close(_to_bars(suspect_rows))
    assert _firstboard_signal(bars, **kwargs) is None


# ============================================================ POOL 盘后扫描


async def test_build_pool_scans_firstboards(session: AsyncSession, repos: Repositories) -> None:
    """盘后扫描：首板日（BOARD_DAY）盘后跑 POOL，8 只种子股中仅 600001 通过全部过滤。"""
    await _seed(session, repos)
    output = await FirstboardDipStrategy().build_pool(_ctx(repos, BOARD_DAY))

    assert [item["code"] for item in output["candidates"]] == ["600001"]
    item = output["candidates"][0]
    assert item["first_board_date"] == BOARD_DAY.isoformat()
    assert item["board_close"] == pytest.approx(11.0)
    assert item["pos_60d"] == pytest.approx(0.5)


# ============================================================ OPENING 开盘确认


async def test_confirm_opening_emits_advice_and_persists(
    session: AsyncSession, repos: Repositories
) -> None:
    """深低开命中 → 产出提醒并落库；卡片键集合与 dragon 完全一致。"""
    await _seed(session, repos, opening_prices={"600001": 10.5})
    output = await FirstboardDipStrategy().confirm_opening(_ctx(repos))

    advices = output["advices"]
    assert len(advices) == 1
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
        "field_snapshot",
    }
    assert advice["buy_day"] == TRADE_DATE.isoformat()
    assert advice["buy_price"] == pytest.approx(10.5)
    assert advice["position"] == pytest.approx(0.20)
    assert advice["stop_loss_price"] is None
    assert advice["path_id"] == "dip"
    assert advice["field_snapshot"]["dip_pct"] == pytest.approx(-0.0455, abs=1e-3)
    assert {gate["factor_id"] for gate in advice["gates"]} == {
        "first_board",
        "pos_60d",
        "dip_bucket",
    }

    rows = await repos.advice_reports.get_by_date(TRADE_DATE, kind="advice")
    assert len(rows) == 1
    assert rows[0].code == "600001"


async def test_confirm_opening_dip_bucket(session: AsyncSession, repos: Repositories) -> None:
    """低开区间过滤：浅低开默认不买、参数放宽后买入、过深低开放弃。"""
    await _seed(session, repos, opening_prices={"600001": 10.8})
    ctx = _ctx(repos)
    strategy = FirstboardDipStrategy()

    # -1.8% 浅低开：不在默认 [-5%, -3%] → 无提醒
    assert (await strategy.confirm_opening(ctx))["advices"] == []

    # 放宽上界到 -1%：同一价格出提醒（override_params 走 contextvars 生效）
    with override_params("firstboard_dip", {"dip_high": -0.01}):
        output = await strategy.confirm_opening(ctx)
    assert len(output["advices"]) == 1

    # -9.1% 过深低开：低于下界 → 无提醒
    await repos.pool_snapshot.upsert_many(
        [
            {
                "trade_date": TRADE_DATE,
                "pool_name": "opening_match",
                "payload": {"600001": {"price": 10.0, "volume_lots": 100, "time_label": "09:25"}},
                "source": "test",
            }
        ]
    )
    await session.commit()
    assert (await strategy.confirm_opening(ctx))["advices"] == []


async def test_confirm_opening_rejects_exrights_artifact(
    session: AsyncSession, repos: Repositories
) -> None:
    """回归（除权护栏）：放宽 dip 带宽后，越界伪跳变（-20%）仍被整票拒收。"""
    await _seed(session, repos, opening_prices={"600001": 8.8})  # 8.8/11.0-1 = -20%
    with override_params("firstboard_dip", {"dip_low": -0.30, "dip_high": -0.10}):
        output = await FirstboardDipStrategy().confirm_opening(_ctx(repos))

    assert output["advices"] == []
    rows = await repos.advice_reports.get_by_date(TRADE_DATE, kind="advice")
    assert rows == []


async def test_confirm_opening_missing_inputs(session: AsyncSession, repos: Repositories) -> None:
    """缺竞价快照或缺交易日历 → 空提醒早退（不臆断）。"""
    ctx = _ctx(repos)
    strategy = FirstboardDipStrategy()

    # 无 opening_match 快照
    await _seed(session, repos)
    assert (await strategy.confirm_opening(ctx))["advices"] == []

    # 无交易日历（无法确定 T-1 首板日）：显式删掉第一次种子写入的日历快照
    await _seed(session, repos, opening_prices={"600001": 10.5}, with_calendar=False)
    await session.execute(delete(PoolSnapshot).where(PoolSnapshot.pool_name == _CALENDAR_POOL))
    await session.commit()
    assert (await strategy.confirm_opening(ctx))["advices"] == []


# ============================================================ run_phase 集成


async def test_run_phase_opening_integration(session: AsyncSession, repos: Repositories) -> None:
    """注册 → run_phase(OPENING) → 提醒产出；UNKNOWN 态显式放行不回退。"""
    await _seed(session, repos, opening_prices={"600001": 10.5})
    register_strategy(FirstboardDipStrategy)
    factory = StrategyContextFactory(repos=repos, settings=get_settings(), trade_date=TRADE_DATE)

    summary = await run_phase(Phase.OPENING, factory, repos)

    assert summary.failure_count == 0
    assert summary.succeeded_ids == ["firstboard_dip"]
    result = summary.results[0]
    assert result.ok is True
    assert len(result.output["advices"]) == 1
    assert result.gate is not None
    assert result.gate.allowed is True
    assert result.gate.position_factor == 1.0
