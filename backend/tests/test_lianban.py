"""lianban_a 插件测试（SQLite 内存库：结构过滤 / 扫池 / 提醒 / 环境否决 / 门控）。

种子场景（涨停池 8 票各自隔离一种判定分支）：

- ``600001`` 正例：61 根 + 首板放量（vr=2.0）+ 二板不缩量 → 通过全部过滤；
- ``300001`` 创业板 → 板块过滤拒；
- ``600002`` ST（stocks.is_st）→ ST 过滤拒；
- ``600003`` boards=4 → 连板天数白名单拒；
- ``600004`` 无日线 → 扫描层守卫拒；
- ``600005`` 首板量比 1.3 → R4 拒；
- ``600901`` / ``600902``（主池型）/ ``600903``（strong 池型）：环境否决计数票
  （日线仅信号日单根 → 结构过滤"日线不足"拒，不干扰候选集）。
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
from app.strategies.plugins.lianban import LianbanStrategy
from app.strategies.plugins.lianban.filters import Bar, Series, structure_filter
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

SIGNAL_DAY = date(2026, 9, 17)  # T-1（信号日，池/日线截至此日）
TRADE_DATE = date(2026, 9, 18)  # T（开盘确认日）

#: 交易日历池名（与策略常量 _CALENDAR_POOL_NAME 一致）。
_CALENDAR_POOL = "trading_calendar"

#: 参与种子的股票代码（见模块 docstring 分支说明）。
CODES = (
    "600001",
    "300001",
    "600002",
    "600003",
    "600004",
    "600005",
    "600901",
    "600902",
    "600903",
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


# ============================================================ 纯函数构造


def _days(last_day: date, total: int) -> list[date]:
    """以 last_day 结尾的 total 个自然日（升序；日线表不校验交易日历）。"""
    return [last_day - timedelta(days=total - 1 - i) for i in range(total)]


def _mk_series(
    rows: list[tuple[float, float, float, float, float]],
    **kwargs: Any,
) -> Series:
    """(open, high, low, close, volume) 元组序列 → Series（自然日递推日期）。"""
    n = len(rows)
    return Series(
        [
            Bar(
                trade_date=_days(SIGNAL_DAY, n)[k],
                open=o,
                high=h,
                low=low,
                close=c,
                volume=v,
                pre_close=None,
            )
            for k, (o, h, low, c, v) in enumerate(rows)
        ],
        **kwargs,
    )


def _flat_row(vol: float = 100.0) -> tuple[float, float, float, float, float]:
    """全平横盘行：开高低收均 10.0（涨跌幅 0%，无板）。"""
    return (10.0, 10.0, 10.0, 10.0, vol)


def _base_rows() -> list[tuple[float, float, float, float, float]]:
    """正例 61 根：59 根横盘 + 首板（放量 200）+ 二板（210，不缩量）。"""
    rows = [_flat_row() for _ in range(59)]
    rows.append((10.0, 11.0, 10.0, 11.0, 200.0))  # 首板：+10%，非一字
    rows.append((11.0, 12.1, 11.0, 12.1, 210.0))  # 二板：+10%，量不萎缩
    return rows


# ============================================================ 种子构造


def _bar(
    code: str,
    day: date,
    o: float,
    h: float,
    low: float,
    c: float,
    vol: int = 1_000_000,
) -> dict[str, Any]:
    """构造一行日线（pre_close 恒为 None，实测插件的回填链路）。"""
    return {
        "code": code,
        "trade_date": day,
        "open": Decimal(str(o)),
        "high": Decimal(str(h)),
        "low": Decimal(str(low)),
        "close": Decimal(str(c)),
        "pre_close": None,
        "volume_shares": vol,
        "amount_yuan": Decimal("10000000"),
        "source": "test",
    }


def _case_bars(code: str) -> list[dict[str, Any]]:
    """按代码构造各自日线场景（见模块 docstring）。"""
    days = _days(SIGNAL_DAY, 61)
    if code == "600001":  # 正例：首板 200w / 二板 210w
        rows = [_bar(code, day, 10.0, 10.0, 10.0, 10.0) for day in days[:59]]
        rows.append(_bar(code, days[59], 10.0, 11.0, 10.0, 11.0, vol=2_000_000))
        rows.append(_bar(code, days[60], 11.0, 12.1, 11.0, 12.1, vol=2_100_000))
        return rows
    if code == "600005":  # R4：首板 130w → 量比 1.3
        rows = [_bar(code, day, 10.0, 10.0, 10.0, 10.0) for day in days[:59]]
        rows.append(_bar(code, days[59], 10.0, 11.0, 10.0, 11.0, vol=1_300_000))
        rows.append(_bar(code, days[60], 11.0, 12.1, 11.0, 12.1, vol=1_500_000))
        return rows
    if code in ("600901", "600902", "600903"):  # 环境计数票：仅信号日单根
        close = {"600901": 10.0, "600902": 20.0, "600903": 10.0}[code]
        return [_bar(code, SIGNAL_DAY, close, close, close, close)]
    # 300001 / 600002 / 600003 / 600004：板块 / ST / boards / 守卫拒，无需日线
    return []


async def _seed(
    session: AsyncSession,
    repos: Repositories,
    *,
    opening_prices: dict[str, float] | None = None,
    with_calendar: bool = True,
) -> None:
    """种入股票主档、日线、涨停池、快照池（交易日历 / 可选竞价撮合）。"""
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
            for code in ("600001", "300001", "600002", "600003", "600005")
        ]
    )
    bars: list[dict[str, Any]] = []
    for code in CODES:
        bars.extend(_case_bars(code))
    await repos.daily_bars.upsert_many(bars)
    pool_rows: list[dict[str, Any]] = []
    pool_meta: dict[str, tuple[str, int]] = {
        "600001": ("limit_up", 2),
        "300001": ("limit_up", 2),
        "600002": ("limit_up", 2),
        "600003": ("limit_up", 4),
        "600005": ("limit_up", 2),
        "600901": ("limit_up", 2),
        "600902": ("limit_up", 2),
        "600903": ("strong", 2),  # 跨池型：环境否决计入、候选扫描不计
    }
    for code, (pool_type, boards) in pool_meta.items():
        pool_rows.append(
            {
                "trade_date": SIGNAL_DAY,
                "pool_type": pool_type,
                "code": code,
                "name": f"股票{code}",
                "continue_days": boards,
                "free_cap_yuan": Decimal("5000000000") if code == "600001" else None,
                "source": "test",
            }
        )
    await repos.limit_up_pool.upsert_many(pool_rows)
    snapshots: list[dict[str, Any]] = []
    if with_calendar:
        snapshots.append(
            {
                "trade_date": TRADE_DATE,
                "pool_name": _CALENDAR_POOL,
                "payload": {"dates": [SIGNAL_DAY.isoformat(), TRADE_DATE.isoformat()]},
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


def _ctx(repos: Repositories, trade_date: date = TRADE_DATE) -> StrategyContext:
    """手工构造策略上下文（params 留空 → 现场解析，override_params 生效）。"""
    return StrategyContext(strategy_id="lianban_a", trade_date=trade_date, repos=repos)


# ============================================================ 门控与声明


def test_gate_matrix() -> None:
    """冰点/退潮禁入，其余五态放行；bear_gate 声明为 True。"""
    for state in (CycleState.ICE, CycleState.RETREAT):
        decision = evaluate_gate(LianbanStrategy, state)
        assert decision.allowed is False
        assert decision.position_factor == 0.0
    for state in (
        CycleState.TURN,
        CycleState.REPAIR,
        CycleState.ACCEL,
        CycleState.DIVERGE,
        CycleState.UNKNOWN,
    ):
        decision = evaluate_gate(LianbanStrategy, state)
        assert decision.allowed is True
        assert decision.position_factor == 1.0


def test_schema_declaration() -> None:
    """策略声明：双阶段 / bear_gate / 13 个参数 / 七态矩阵。"""
    strategy = LianbanStrategy()
    assert strategy.strategy_id == "lianban_a"
    assert strategy.label == "连板捉妖"
    assert Phase.POOL in strategy.phases
    assert Phase.OPENING in strategy.phases
    assert strategy.bear_gate is True
    keys = {spec.key for spec in strategy.params_schema}
    assert keys == {
        "scene_a_low_open",
        "min_first_board_volume_ratio",
        "max_position_ratio",
        "wave_lookback_days",
        "limit_up_pct",
        "one_word_pct",
        "structure_window",
        "min_window_bars",
        "position_window",
        "auction_limit_down_pct",
        "max_auction_limit_down",
        "position",
        "max_total_position",
    }
    assert set(strategy.gate_matrix) == set(CycleState)


# ============================================================ 结构过滤纯函数


def test_structure_filter_positive() -> None:
    """正例：2 板放量结构通过，产出 vr=2.0 / pos=1.0。"""
    result = structure_filter(_mk_series(_base_rows()), 60, 2)
    assert result.ok is True
    assert result.fail_reason == ""
    assert result.vr == pytest.approx(2.0)
    assert result.pos == pytest.approx(1.0)


def test_structure_filter_r1_not_first_wave() -> None:
    """R1：当前波首板之前 19 个交易日（≤20）存在 ≥2 板波 → 拒。"""
    rows = _base_rows()
    rows[39] = (10.0, 11.0, 10.0, 11.0, 100.0)  # 前波首板：10 → 11
    rows[40] = (11.0, 12.1, 11.0, 12.1, 100.0)  # 前波二板：11 → 12.1
    for k in range(41, 59):  # 前波后横盘抬升至 12.1，避免跳变触发脏数据护栏
        rows[k] = (12.1, 12.1, 12.1, 12.1, 100.0)
    rows[59] = (12.1, 13.31, 12.1, 13.31, 200.0)
    rows[60] = (13.31, 14.64, 13.31, 14.64, 210.0)
    result = structure_filter(_mk_series(rows), 60, 2)
    assert result.ok is False
    assert result.fail_reason == "R1非第一波"


def test_structure_filter_r2_three_boards() -> None:
    """R2：三板组——首板量 ≤基量×1.2 且 板2/板3 一字缩量至 5% → 拒。"""
    rows = [_flat_row() for _ in range(58)]
    rows.append((10.0, 11.0, 10.0, 11.0, 110.0))  # 首板 110 ≤ 100×1.2
    rows.append((12.1, 12.1, 12.1, 12.1, 5.0))  # 板2 一字缩量
    rows.append((13.31, 13.31, 13.31, 13.31, 5.0))  # 板3 一字缩量
    result = structure_filter(_mk_series(rows), 60, 3)
    assert result.ok is False
    assert result.fail_reason == "R2三板组"


def test_structure_filter_r3_volume_shrink() -> None:
    """R3：波内任一日量低于基量（二板 90 < 首板前日 100）→ 拒。"""
    rows = _base_rows()
    rows[60] = (11.0, 12.1, 11.0, 12.1, 90.0)
    result = structure_filter(_mk_series(rows), 60, 2)
    assert result.ok is False
    assert result.fail_reason == "R3量能萎缩"


def test_structure_filter_r4_first_board_vr() -> None:
    """R4：首板量比 1.3 < 1.5 → 拒。"""
    rows = _base_rows()
    rows[59] = (10.0, 11.0, 10.0, 11.0, 130.0)
    result = structure_filter(_mk_series(rows), 60, 2)
    assert result.ok is False
    assert result.fail_reason == "R4首板量比"


def test_structure_filter_r5_position() -> None:
    """R5：首板前一日开盘 / 末 30 根最低 = 1.16 ≥ 1.15 → 拒。"""
    rows = _base_rows()
    rows[58] = (11.6, 11.6, 10.0, 10.0, 100.0)
    result = structure_filter(_mk_series(rows), 60, 2)
    assert result.ok is False
    assert result.fail_reason == "R5位置过滤"


def test_structure_filter_r0_suspect() -> None:
    """R0：窗口内任一涨跌幅绝对值 >10.5%（脏数据护栏）→ 拒。"""
    rows = _base_rows()
    rows[30] = (10.0, 11.06, 10.0, 11.06, 100.0)  # +10.6%
    result = structure_filter(_mk_series(rows), 60, 2)
    assert result.ok is False
    assert result.fail_reason == "R0suspect"


def test_structure_filter_days_mismatch() -> None:
    """口径不一致：池 boards=2 但日线当前波仅 1 板 → 拒。"""
    rows = _base_rows()
    rows[59] = _flat_row()  # 首板改横盘 → 当前波仅末根 1 板
    rows[60] = (10.0, 11.0, 10.0, 11.0, 200.0)
    result = structure_filter(_mk_series(rows), 60, 2)
    assert result.ok is False
    assert result.fail_reason.startswith("口径不一致")


def test_structure_filter_not_enough_bars() -> None:
    """日线不足：窗口仅 5 根（<10）→ 拒。"""
    result = structure_filter(_mk_series(_base_rows()[:5]), 4, 2)
    assert result.ok is False
    assert result.fail_reason == "日线不足"


# ============================================================ 阈值 / 窗口参数化


def test_series_threshold_params() -> None:
    """涨停 / 一字阈值可插拔：+9.85% 开盘首板默认非一字，调低一字阈值后判一字。"""
    rows = _base_rows()
    rows[59] = (10.985, 11.0, 10.985, 11.0, 200.0)  # 开/低 +9.85%，收 +10%
    default = _mk_series(rows)
    assert default.is_lu[59] is True
    assert default.one_word[59] is False
    tuned = _mk_series(rows, one_word_pct=0.098)
    assert tuned.one_word[59] is True


def test_structure_filter_position_window_param() -> None:
    """位置窗口可插拔：默认末 30 根（索引 31..60）放行，放宽到 60 根后触发 R5。"""
    rows = _base_rows()
    rows[25] = (10.0, 10.0, 8.0, 10.0, 100.0)  # 低价坑：默认窗口之外
    i = 60
    assert structure_filter(_mk_series(rows), i, 2).ok is True
    result = structure_filter(_mk_series(rows), i, 2, position_window=60)
    assert result.ok is False
    assert result.fail_reason == "R5位置过滤"


def test_structure_filter_min_window_bars_param() -> None:
    """最少日线根数可插拔：3 根横盘 + 2 板结构，默认拒，收紧阈值到 3 根后放行。"""
    rows = [_flat_row() for _ in range(3)]
    rows.append((10.0, 11.0, 10.0, 11.0, 200.0))
    rows.append((11.0, 12.1, 11.0, 12.1, 210.0))
    series = _mk_series(rows)
    assert structure_filter(series, 4, 2).ok is False
    assert structure_filter(series, 4, 2).fail_reason == "日线不足"
    result = structure_filter(series, 4, 2, min_window_bars=3)
    assert result.ok is True
    assert result.fail_reason == ""


# ============================================================ POOL 盘后扫池


async def test_build_pool_scans_pool(session: AsyncSession, repos: Repositories) -> None:
    """盘后扫池：8 票种子中仅 600001 通过板块 / ST / boards / 结构全过滤。"""
    await _seed(session, repos)
    output = await LianbanStrategy().build_pool(_ctx(repos, SIGNAL_DAY))

    assert [item["code"] for item in output["candidates"]] == ["600001"]
    item = output["candidates"][0]
    assert item["boards"] == 2
    assert item["vr"] == pytest.approx(2.0)
    assert item["pos"] == pytest.approx(1.0)
    assert item["prev_close"] == pytest.approx(12.1)


async def test_filter_window_params_propagate(
    session: AsyncSession, repos: Repositories
) -> None:
    """参数贯通：filters 常量仅为默认值，override 经 params_schema 影响扫池。"""
    await _seed(session, repos)
    ctx = _ctx(repos, SIGNAL_DAY)
    strategy = LianbanStrategy()
    assert [item["code"] for item in (await strategy.build_pool(ctx))["candidates"]] == ["600001"]

    # 涨停阈值抬到 15%：日线无涨停日 → 当前波 1 ≠ 池 2 → 口径不一致拒
    with override_params("lianban_a", {"limit_up_pct": 0.15}):
        output = await strategy.build_pool(ctx)
    assert output["candidates"] == []

    # 结构窗口收窄到 20 根（回看自然日同步收窄）、最少根数抬到 30 → 日线不足拒
    with override_params("lianban_a", {"structure_window": 20, "min_window_bars": 30}):
        output = await strategy.build_pool(ctx)
    assert output["candidates"] == []


# ============================================================ OPENING 开盘确认


async def test_confirm_opening_emits_advice_and_persists(
    session: AsyncSession, repos: Repositories
) -> None:
    """场景 A 深低开命中 → 产出提醒并落库；卡片键集合与 dragon 完全一致。"""
    await _seed(session, repos, opening_prices={"600001": 11.4})  # -5.79%
    output = await LianbanStrategy().confirm_opening(_ctx(repos))

    assert output["env_vetoed"] is False
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
    assert advice["buy_price"] == pytest.approx(11.4)
    assert advice["position"] == pytest.approx(0.20)
    assert advice["stop_loss_price"] is None
    assert advice["path_id"] == "A"
    assert advice["field_snapshot"]["open_gap_pct"] == pytest.approx(-0.0579, abs=1e-3)
    assert advice["field_snapshot"]["boards"] == 2
    assert advice["field_snapshot"]["vr"] == pytest.approx(2.0)
    assert advice["field_snapshot"]["prev_close"] == pytest.approx(12.1)
    assert advice["field_snapshot"]["float_mv_yuan"] == pytest.approx(5_000_000_000)
    assert {gate["factor_id"] for gate in advice["gates"]} == {
        "lianban_structure",
        "volume_structure",
        "position_low",
        "scene_a",
    }

    rows = await repos.advice_reports.get_by_date(TRADE_DATE, kind="advice")
    assert len(rows) == 1
    assert rows[0].code == "600001"


async def test_confirm_opening_scene_gate(session: AsyncSession, repos: Repositories) -> None:
    """场景 A 单边门：浅低开（-4.96%）默认不买，放宽阈值后买入。"""
    await _seed(session, repos, opening_prices={"600001": 11.5})
    ctx = _ctx(repos)
    strategy = LianbanStrategy()

    assert (await strategy.confirm_opening(ctx))["advices"] == []

    with override_params("lianban_a", {"scene_a_low_open": -0.04}):
        output = await strategy.confirm_opening(ctx)
    assert len(output["advices"]) == 1


async def test_confirm_opening_rejects_exrights_artifact(
    session: AsyncSession, repos: Repositories
) -> None:
    """回归（除权护栏）：-50% 除权伪跳变（10 转 10 未复权）不再被单边门放行。"""
    await _seed(session, repos, opening_prices={"600001": 6.05})  # 6.05/12.1-1 = -50%
    output = await LianbanStrategy().confirm_opening(_ctx(repos))

    assert output["env_vetoed"] is False
    assert output["advices"] == []
    rows = await repos.advice_reports.get_by_date(TRADE_DATE, kind="advice")
    assert rows == []


async def test_confirm_opening_env_veto(session: AsyncSession, repos: Repositories) -> None:
    """环境否决：连板票（boards≥2、跨池型）竞价跌停 3 家 >2 → 全策略停买。"""
    await _seed(
        session,
        repos,
        opening_prices={"600001": 11.4, "600901": 9.0, "600902": 18.0, "600903": 9.0},
    )
    output = await LianbanStrategy().confirm_opening(_ctx(repos))

    assert output["env_vetoed"] is True
    assert output["auction_limit_down_count"] == 3
    assert output["advices"] == []
    rows = await repos.advice_reports.get_by_date(TRADE_DATE, kind="advice")
    assert rows == []


async def test_confirm_opening_missing_inputs(session: AsyncSession, repos: Repositories) -> None:
    """缺竞价快照或缺交易日历 → 空提醒早退（不臆断）。"""
    ctx = _ctx(repos)
    strategy = LianbanStrategy()

    # 无 opening_match 快照
    await _seed(session, repos)
    assert (await strategy.confirm_opening(ctx))["advices"] == []

    # 无交易日历（无法确定 T-1 信号日）：显式删掉第一次种子写入的日历快照
    await _seed(session, repos, opening_prices={"600001": 11.4}, with_calendar=False)
    await session.execute(delete(PoolSnapshot).where(PoolSnapshot.pool_name == _CALENDAR_POOL))
    await session.commit()
    assert (await strategy.confirm_opening(ctx))["advices"] == []


# ============================================================ run_phase 集成


async def test_run_phase_opening_integration(session: AsyncSession, repos: Repositories) -> None:
    """注册 → run_phase(OPENING) → 提醒产出；UNKNOWN 态显式放行不回退。"""
    await _seed(session, repos, opening_prices={"600001": 11.4})
    register_strategy(LianbanStrategy)
    factory = StrategyContextFactory(repos=repos, settings=get_settings(), trade_date=TRADE_DATE)

    summary = await run_phase(Phase.OPENING, factory, repos)

    assert summary.failure_count == 0
    assert summary.succeeded_ids == ["lianban_a"]
    result = summary.results[0]
    assert result.ok is True
    assert len(result.output["advices"]) == 1
    assert result.gate is not None
    assert result.gate.allowed is True
    assert result.gate.position_factor == 1.0
