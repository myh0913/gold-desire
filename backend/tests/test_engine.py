"""引擎单元测试：卖出规则 / 样本构建 / 三段切分 / 组合风控 / 回测落库。

全程使用内存 SQLite + 合成数据，**不触网、不依赖 PostgreSQL / Redis**。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import date
from decimal import Decimal

import pytest
from app.db.base import Base
from app.engine.dragon_samples import DayMetrics, DragonSample, build_samples
from app.engine.portfolio import (
    GateSpec,
    PathSpec,
    sim,
)
from app.engine.segments import (
    SEGMENT_A,
    SEGMENT_ALL,
    SEGMENT_B,
    SEGMENT_C,
    SNAPSHOT_CUTS,
    split_segments,
)
from app.engine.sell_rules import EXIT_CLOSE, EXIT_STOP_LOSS, SellRuleResult, evaluate
from app.models.market import DailyBar, MinuteBar, Stock
from app.repositories import Repositories
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ============================================================ fixtures


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 会话（每个用例独立建库）。"""
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


# ============================================================ 卖出规则


def test_sell_rule_no_take_profit_settles_at_close() -> None:
    """默认无止盈：未触发止损则按可卖日收盘价了结。"""
    result = evaluate(100.0, ((100.0, 99.0, 98.0, 101.0),))
    assert result is not None
    assert result.exit_reason == EXIT_CLOSE
    assert result.return_pct == pytest.approx(0.01)
    assert result.exit_index == 3


def test_sell_rule_stop_loss_returns_exactly_threshold() -> None:
    """止损触发时按止损价成交（返回恰好 -0.03，无跳空假设）。"""
    result = evaluate(100.0, ((100.0, 97.0, 96.0),))
    assert result is not None
    assert result.exit_reason == EXIT_STOP_LOSS
    assert result.return_pct == pytest.approx(-0.03)
    assert result.exit_index == 1


def test_sell_rule_stop_loss_only_on_sellable_day() -> None:
    """止损仅在可卖日生效：买入当日不在遍历序列中，故当日下跌不触发止损。"""
    # 买入当日（D+1）暴跌 5%，但不作为可卖日序列传入；可卖日（D+2）仅小幅波动。
    result = evaluate(100.0, ((100.0, 99.5, 100.2),))
    assert result is not None
    assert result.exit_reason == EXIT_CLOSE
    assert result.return_pct == pytest.approx(0.002)


def test_sell_rule_hold_two_days_concatenates_sellable_days() -> None:
    """持 2 个可卖日：两日序列按序拼接后统一遍历。"""
    single = evaluate(100.0, ((101.0, 102.0), (103.0,)))
    double = evaluate(100.0, ((101.0, 102.0), (103.0,)), hold_sellable_days=2)
    assert single is not None and double is not None
    assert single.return_pct == pytest.approx(0.02)
    assert double.return_pct == pytest.approx(0.03)


def test_sell_rule_take_profit_when_explicitly_enabled() -> None:
    """显式开启止盈时才止盈（默认不设）。"""
    result = evaluate(100.0, ((101.0, 103.0),), take_profit=0.02)
    assert result is not None
    assert result.exit_reason == "take_profit"
    assert result.return_pct == pytest.approx(0.02)


def test_sell_rule_one_word_limit_down_fallback_is_close() -> None:
    """一字跌停无法卖出：回测按收盘价成交（乐观假设）；不设止损时可见 -10%。"""
    result = evaluate(100.0, ((90.0, 90.0, 90.0),), stop_loss=None)
    assert result is not None
    assert result.exit_reason == EXIT_CLOSE
    assert result.return_pct == pytest.approx(-0.10)
    # 设了止损则更早离场（回测口径；实盘一字跌停无法成交，真实结果更差）
    with_stop = evaluate(100.0, ((90.0, 90.0, 90.0),))
    assert with_stop is not None and with_stop.return_pct == pytest.approx(-0.03)


def test_sell_rule_missing_inputs_return_none() -> None:
    """买价缺失 / 序列为空时返回 ``None``。"""
    assert evaluate(None, ((100.0,),)) is None
    assert evaluate(100.0, ()) is None


def test_sell_rule_reports_max_gain_and_drawdown() -> None:
    """返回最大浮盈 / 最大浮亏（供「均上冲 / 均回撤」统计）。

    止损触发时实现收益为 -3%（成交价），但路径最大浮亏可能更深（此例 -4%）。
    """
    result: SellRuleResult | None = evaluate(100.0, ((105.0, 96.0, 104.0),))
    assert result is not None
    assert result.return_pct == pytest.approx(-0.03)
    assert result.max_gain == pytest.approx(0.05)
    assert result.max_drawdown == pytest.approx(-0.04)


# ============================================================ 三段切分


def _dated(day: date, code: str = "600000.SH") -> DragonSample:
    """构造仅含基准日的最小样本（切分测试用）。"""
    return DragonSample(code=code, name="t", D=day, T=day, T1=day)


def test_split_segments_is_rank_position_and_non_overlapping() -> None:
    """A/B/C 按秩位置中位数切分，三段互不重叠且并集等于全量。"""
    days = [date(2026, 5, 1 + offset) for offset in range(9)]
    samples = [_dated(day) for day in days]

    segments = split_segments(samples)

    cut_ab, cut_bc = segments.cuts
    assert cut_bc == SNAPSHOT_CUTS[1]
    assert cut_ab == days[4]  # 9 条的中位记录（索引 4）
    assert segments.counts() == {SEGMENT_A: 4, SEGMENT_B: 5, SEGMENT_C: 0, SEGMENT_ALL: 9}
    merged = (
        [sample.code + sample.D.isoformat() for sample in segments.get(SEGMENT_A)]
        + [sample.code + sample.D.isoformat() for sample in segments.get(SEGMENT_B)]
        + [sample.code + sample.D.isoformat() for sample in segments.get(SEGMENT_C)]
    )
    assert sorted(merged) == sorted(sample.code + sample.D.isoformat() for sample in samples)


def test_split_segments_accepts_explicit_snapshot_cuts() -> None:
    """显式切点优先（readme §14.7：切点是快照，可固定以保证复现）。"""
    samples = [_dated(date(2026, 1, 1)), _dated(date(2026, 2, 1)), _dated(date(2026, 3, 1))]
    segments = split_segments(samples, cuts=(date(2026, 1, 15), date(2026, 2, 15)))
    assert segments.counts() == {SEGMENT_A: 1, SEGMENT_B: 1, SEGMENT_C: 1, SEGMENT_ALL: 3}


# ============================================================ 样本构建（合成数据）


def _daily(
    code: str,
    day: date,
    *,
    open_px: float,
    high: float,
    low: float,
    close: float,
    pre_close: float,
    volume: int,
) -> DailyBar:
    """构造一根日线。"""
    return DailyBar(
        code=code,
        trade_date=day,
        open=Decimal(str(open_px)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        pre_close=Decimal(str(pre_close)),
        volume_shares=volume,
        amount_yuan=Decimal(str(volume * close)),
    )


def _minutes(code: str, day: date, prices: Sequence[float]) -> list[MinuteBar]:
    """构造 240 点分时（价格序列；成交量置 1 手）。"""
    return [
        MinuteBar(
            code=code,
            trade_date=day,
            minute_index=index,
            time_label=f"{index:03d}",
            price=Decimal(str(price)),
            volume_lots=1,
            amount_yuan=Decimal(str(price)),
        )
        for index, price in enumerate(prices)
    ]


async def _seed_wave(session: AsyncSession, code: str, board: str = "主板") -> None:
    """写入一只股票的「2 连板 + 首阴 + T/T1/T2」合成数据。"""
    session.add(Stock(code=code, name="测试股", market="SH", board=board, is_st=False))
    bars = [
        # 连板波：首板 + 一字板
        _daily(
            code,
            date(2026, 1, 5),
            open_px=10.5,
            high=11.0,
            low=10.4,
            close=11.0,
            pre_close=10.0,
            volume=1_000,
        ),
        _daily(
            code,
            date(2026, 1, 6),
            open_px=12.1,
            high=12.1,
            low=12.1,
            close=12.1,
            pre_close=11.0,
            volume=2_000,
        ),
        # 首阴 D：收盘 < 开盘；振幅 (12.8-11.5)/12.1
        _daily(
            code,
            date(2026, 1, 7),
            open_px=12.6,
            high=12.8,
            low=11.5,
            close=11.6,
            pre_close=12.1,
            volume=3_000,
        ),
        # T / T1 / T2
        _daily(
            code,
            date(2026, 1, 8),
            open_px=11.0,
            high=11.8,
            low=10.9,
            close=11.7,
            pre_close=11.6,
            volume=900,
        ),
        _daily(
            code,
            date(2026, 1, 9),
            open_px=11.6,
            high=12.0,
            low=11.4,
            close=11.9,
            pre_close=11.7,
            volume=1_100,
        ),
        _daily(
            code,
            date(2026, 1, 12),
            open_px=11.9,
            high=12.2,
            low=11.7,
            close=12.1,
            pre_close=11.9,
            volume=1_200,
        ),
    ]
    session.add_all(bars)

    # D 日分时：前 200 点高位横盘，尾盘跳水至 11.5（最低点索引 200 → 尾盘跳水）
    d_prices = [12.6] * 200 + [11.5] * 40
    session.add_all(_minutes(code, date(2026, 1, 7), d_prices))
    session.add_all(_minutes(code, date(2026, 1, 8), [11.0] * 240))
    session.add_all(_minutes(code, date(2026, 1, 9), [11.6] * 240))
    await session.commit()


async def test_build_samples_derives_fields_from_daily_and_minute(
    repos: Repositories, session: AsyncSession
) -> None:
    """合成数据：连板数 / 首阴振幅 / t_vol_vs_d / low_time_i / shape_label 派生正确。"""
    await _seed_wave(session, "600001.SH")

    samples = await build_samples(repos, date(2026, 1, 7), date(2026, 1, 7))

    assert len(samples) == 1
    sample = samples[0]
    assert sample.code == "600001.SH"
    assert sample.boards == 2
    assert sample.wave_one_word_cnt == 1
    assert sample.wave_first_vol == pytest.approx(1_000.0)
    assert sample.wave_last_vol == pytest.approx(2_000.0)
    assert sample.wave_peak_vol == pytest.approx(2_000.0)
    assert sample.wave_vol_trend == pytest.approx(2.0)
    assert sample.d_amp_pct == pytest.approx((12.8 - 11.5) / 12.1)
    assert sample.vol_vs_prev == pytest.approx(3_000 / 2_000)
    assert sample.vol_vs_wavepeak == pytest.approx(3_000 / 2_000)
    assert sample.vol_vs_wavemean == pytest.approx(3_000 / 1_500)
    assert sample.t_vol_vs_d == pytest.approx(900 / 3_000)
    assert sample.mp_D.low_time_i == 200
    assert sample.mp_D.close_pos == pytest.approx(0.0)
    assert sample.shape_label == "尾盘跳水"
    assert date(2026, 1, 8) == sample.T
    assert date(2026, 1, 9) == sample.T1
    assert date(2026, 1, 12) == sample.T2


async def test_build_samples_excludes_non_main_board(
    repos: Repositories, session: AsyncSession
) -> None:
    """只取 60/00 主板；创业板（30）被排除。"""
    await _seed_wave(session, "300001.SZ", board="创业板")

    samples = await build_samples(repos, date(2026, 1, 7), date(2026, 1, 7))
    assert samples == []


# ============================================================ 组合风控（仓位上限）


def _sample(day: date, code: str, buy_price: float, px_t1: tuple[float, ...]) -> DragonSample:
    """构造一条可被「恒真门槛」路径命中的样本。"""
    return DragonSample(
        code=code,
        name="t",
        D=day,
        T=day,
        T1=day,
        px_T1=px_t1,
        t=DayMetrics(open=buy_price),
    )


def _always_path(*, base_position: float) -> PathSpec:
    """构造一条恒命中、固定仓位的路径（仅用于组合风控单测）。"""
    return PathSpec(
        path_id="T",
        label="恒真",
        priority=1,
        buy_day_field="T",
        buy_metrics_field="T",
        sellable_day_fields=("T1",),
        base_position=base_position,
        max_position_factor=1.0,
        scale_by_bonus=False,
        gates=(
            GateSpec(
                factor_id="dragon",
                label="恒真",
                test=lambda ctx, params: True,
                describe=lambda ctx, params: "always",
            ),
        ),
        bonus=(),
    )


def test_portfolio_caps_same_day_position_proportionally() -> None:
    """同日总仓位超 80% 时等比压缩，压缩后恰为 80%。"""
    day = date(2026, 1, 8)
    samples = [_sample(day, f"60000{index}.SH", 100.0, (101.0, 102.0)) for index in range(5)]
    result = sim(
        [_always_path(base_position=0.20)],
        samples,
        factor_params={"dragon": {}},
    )

    assert result.stats.trigger_count == 5
    assert result.stats.trading_days == 1
    assert result.stats.avg_usage == pytest.approx(0.80)
    # 每笔 +2%，压缩后当日收益 = 0.80 * 2% = 1.6%
    assert result.stats.cumulative_return == pytest.approx(0.016)


def test_portfolio_below_cap_keeps_full_position() -> None:
    """未超上限时不压缩。"""
    day = date(2026, 1, 8)
    samples = [_sample(day, "600000.SH", 100.0, (101.0,))]
    result = sim([_always_path(base_position=0.20)], samples, factor_params={"dragon": {}})
    assert result.stats.avg_usage == pytest.approx(0.20)
    assert result.stats.cumulative_return == pytest.approx(0.002)


# ============================================================ 回测落库


async def test_run_backtest_persists_to_backtest_runs(
    repos: Repositories, session: AsyncSession
) -> None:
    """回测结果经 BacktestRunRepository 幂等落库（同 run_id 重跑不新增行）。"""
    from app.engine.backtest import run_backtest

    day = date(2026, 1, 8)
    samples = [_sample(day, "600000.SH", 100.0, (101.0,))]
    paths = [_always_path(base_position=0.20)]

    first = await run_backtest(
        samples,
        {"base_position": 0.20},
        paths=paths,
        factor_params={"dragon": {}},
        repos=repos,
        run_id="run-1",
    )
    second = await run_backtest(
        samples,
        {"base_position": 0.20},
        paths=paths,
        factor_params={"dragon": {}},
        repos=repos,
        run_id="run-1",
    )

    assert first.to_dict() == second.to_dict()
    rows = await repos.backtest_runs.list_recent()
    assert len(rows) == 1
    assert rows[0].status == "succeeded"
    assert rows[0].report is not None
    assert rows[0].report["trigger_count"] == 1
