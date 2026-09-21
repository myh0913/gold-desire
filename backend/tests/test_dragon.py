"""龙回头策略验收测试：复现 readme 一年数据基线（S2 / S4 / 组合 / 触发 311）。

数据来源为已验证样本夹具（``tests/fixtures/dragon_samples.json``，1180 条 / 1179 条有效），
经 :mod:`app.engine.dragon_legacy` 适配器转为生产类型 :class:`DragonSample`，再走**真实**的
策略（:class:`DragonStrategy`）+ 卖出规则（:mod:`app.engine.sell_rules`）+
组合引擎（:mod:`app.engine.portfolio` / :mod:`app.engine.backtest`）代码路径。

全程无网络、无 PostgreSQL / Redis（夹具 + 内存 SQLite）。
百分比容差 ±0.02pp；计数精确相等。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from app.db.base import Base
from app.engine.backtest import default_factor_params, run_backtest
from app.engine.dragon_legacy import load_fixture, load_fixture_records
from app.engine.dragon_samples import DayMetrics, DragonSample, MinuteMetrics
from app.engine.portfolio import GateSpec, PathSpec
from app.engine.segments import SEGMENT_A, SEGMENT_ALL, SEGMENT_B, SEGMENT_C
from app.engine.sell_rules import EXIT_CLOSE, EXIT_STOP_LOSS, SellRuleConfig
from app.models.market import DailyBar, MinuteBar, Stock
from app.repositories import Repositories
from app.strategies.context import StrategyContext
from app.strategies.plugins.dragon.gates import GATE_MATRIX, number, param_value
from app.strategies.plugins.dragon.paths import PATH_S2, PATH_S4
from app.strategies.plugins.dragon.strategy import KIND_ADVICE, DragonStrategy
from app.strategies.registry import all_strategies, discover_plugins, get_strategy
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

#: 百分比容差（±0.02pp，小数口径）。
TOL = 0.02 / 100


def _pct(value: float) -> float:
    """小数口径转百分数（仅用于可读断言消息）。"""
    return value * 100


def _assert_pct(actual: float, expected: float) -> None:
    """断言小数口径百分比在 ±0.02pp 内。"""
    assert actual == pytest.approx(expected / 100, abs=TOL), (
        f"实际 {_pct(actual):+.4f}% vs 基线 {expected:+.2f}%"
    )


# ============================================================ fixtures（模块级，夹具只读一次）


@pytest.fixture(scope="module")
def samples() -> list[DragonSample]:
    """已验证样本（1179 条有效）。"""
    return load_fixture()


@pytest.fixture(scope="module")
def strategy() -> DragonStrategy:
    """龙回头策略实例。"""
    return DragonStrategy()


@pytest.fixture(scope="module")
def params(strategy: DragonStrategy) -> dict[str, object]:
    """策略代码默认参数。"""
    return strategy.default_params()


@pytest.fixture(scope="module")
def factor_params(
    strategy: DragonStrategy, params: dict[str, object]
) -> dict[str, dict[str, object]]:
    """因子参数（阈值唯一来源）+ 策略命名空间。"""
    return strategy.build_factor_params(params, default_factor_params(strategy.factor_param_ids()))


@pytest.fixture(scope="module")
def paths(strategy: DragonStrategy, params: dict[str, object]) -> list[PathSpec]:
    """两路路径（S2 / S4）。"""
    return strategy.build_paths(params)


async def _report(
    samples: list[DragonSample],
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
    paths: list[PathSpec],
    **kwargs: object,
) -> object:
    """跑一次回测。"""
    return await run_backtest(samples, params, paths=paths, factor_params=factor_params, **kwargs)


# ============================================================ 适配器 / 插件发现


def test_legacy_fixture_keeps_all_records_and_filters_to_1179() -> None:
    """夹具保留全部 1180 条；有效样本 1179 条（剔除缺 ``_px_T`` 的记录）。"""
    records = load_fixture_records()
    assert len(records) == 1180

    samples = load_fixture()
    assert len(samples) == 1179
    assert {sample.code for sample in samples}  # 非空
    assert samples[0].D <= samples[-1].D


def test_dragon_plugin_is_discovered_and_registered() -> None:
    """目录式发现自动注册 ``dragon``（核心零改动）。"""
    result = discover_plugins(strict=True)
    assert "dragon" in {cls.strategy_id for cls in result.strategies}
    assert "dragon" in {cls.strategy_id for cls in all_strategies()}

    cls = get_strategy("dragon")
    assert cls.label == "龙回头"
    assert cls.strategy_id == "dragon"


def test_gate_matrix_is_declared_on_strategy(strategy: DragonStrategy) -> None:
    """周期门控矩阵由策略自身声明（核心只按协议读取）。"""
    assert strategy.gate_matrix == GATE_MATRIX
    assert strategy.gate_matrix_dict()["冰点"] == {"allowed": False, "position_factor": 0.0}
    assert strategy.gate_matrix_dict()["修复"] == {"allowed": True, "position_factor": 1.0}


# ============================================================ 基线复现


async def test_s2_baseline_numbers(
    samples: list[DragonSample],
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
    paths: list[PathSpec],
) -> None:
    """S2：n=180，全量 +2.51% / A +3.00% / B +2.57% / C +1.89%。"""
    report = await _report(samples, params, factor_params, paths)
    assert report.trigger_count == 311  # type: ignore[attr-defined]

    expected = {
        SEGMENT_ALL: (180, 2.51),
        SEGMENT_A: (60, 3.00),
        SEGMENT_B: (65, 2.57),
        SEGMENT_C: (55, 1.89),
    }
    for name, (count, mean) in expected.items():
        segment = report.segment(name)  # type: ignore[attr-defined]
        stats = segment.path(PATH_S2)
        assert stats.n == count, f"{name}: S2 样本数 {stats.n} != {count}"
        _assert_pct(stats.mean_return, mean)


async def test_s4_baseline_numbers(
    samples: list[DragonSample],
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
    paths: list[PathSpec],
) -> None:
    """S4：n=165，全量 +2.83% / A +2.06% / B +2.78% / C +3.64%。"""
    report = await _report(samples, params, factor_params, paths)

    expected = {
        SEGMENT_ALL: (165, 2.83),
        SEGMENT_A: (51, 2.06),
        SEGMENT_B: (62, 2.78),
        SEGMENT_C: (52, 3.64),
    }
    for name, (count, mean) in expected.items():
        segment = report.segment(name)  # type: ignore[attr-defined]
        stats = segment.path(PATH_S4)
        assert stats.n == count, f"{name}: S4 样本数 {stats.n} != {count}"
        _assert_pct(stats.mean_return, mean)


async def test_combined_baseline_and_trigger_count_311(
    samples: list[DragonSample],
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
    paths: list[PathSpec],
) -> None:
    """组合（推荐版）：A +64.85% / B +76.97% / C +65.23% / 全量 +382.71%，回撤 -7.79%。

    触发数 **311**（按样本去重后；非两路简单相加 345）——这是 S2>S4 优先级去重的行为。
    """
    report = await _report(samples, params, factor_params, paths)

    expected = {
        SEGMENT_A: (102, 64.85, -7.79),
        SEGMENT_B: (115, 76.97, -3.85),
        SEGMENT_C: (94, 65.23, -5.89),
        SEGMENT_ALL: (311, 382.71, -7.79),
    }
    for name, (triggers, cumulative, drawdown) in expected.items():
        segment = report.segment(name)  # type: ignore[attr-defined]
        portfolio = segment.portfolio
        assert portfolio.trigger_count == triggers, f"{name}: 触发数 {portfolio.trigger_count}"
        _assert_pct(portfolio.cumulative_return, cumulative)
        _assert_pct(portfolio.max_drawdown, drawdown)

    # 触发数 311 ≠ 345（两路各自 180 + 165 = 345，重叠 34 次按优先级只计一次）
    assert report.trigger_count == 311  # type: ignore[attr-defined]
    assert report.trigger_count != 180 + 165  # type: ignore[attr-defined]


async def test_report_always_has_per_segment_output(
    samples: list[DragonSample],
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
    paths: list[PathSpec],
) -> None:
    """报告始终含 A/B/C 三段 + 全量（禁止只报全量）。"""
    report = await _report(samples, params, factor_params, paths)
    names = [segment.name for segment in report.segments]  # type: ignore[attr-defined]
    assert names == [SEGMENT_A, SEGMENT_B, SEGMENT_C, SEGMENT_ALL]
    for segment in report.segments:  # type: ignore[attr-defined]
        assert segment.path(PATH_S2) is not None
        assert segment.path(PATH_S4) is not None
        assert segment.sample_count > 0


async def test_backtest_is_idempotent(
    samples: list[DragonSample],
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
    paths: list[PathSpec],
) -> None:
    """同输入重跑 ⇒ 输出完全一致（幂等）。"""
    first = await _report(samples, params, factor_params, paths)
    second = await _report(samples, params, factor_params, paths)
    assert first.to_dict() == second.to_dict()  # type: ignore[attr-defined]


async def test_caliber_contrast_matches_readme(
    samples: list[DragonSample],
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
    paths: list[PathSpec],
) -> None:
    """口径对照：推荐版（S4 加码）C +65.23% vs 基础版（不加码）C +51.90%。"""
    report = await _report(samples, params, factor_params, paths)
    _assert_pct(report.caliber["推荐版"][SEGMENT_C].cumulative_return, 65.23)  # type: ignore[attr-defined]
    _assert_pct(report.caliber["基础版"][SEGMENT_C].cumulative_return, 51.90)  # type: ignore[attr-defined]


# ============================================================ 阈值来自因子参数


async def test_min_amplitude_change_changes_selection(
    samples: list[DragonSample],
    strategy: DragonStrategy,
    params: dict[str, object],
    paths: list[PathSpec],
) -> None:
    """把 ``min_amplitude`` 从 0.08 改为 0.07，选股范围变化（证明阈值取自因子参数）。"""
    ids = strategy.factor_param_ids()
    tuned = strategy.build_factor_params(params, default_factor_params(ids))
    tuned["first_yin_amplitude"] = {**tuned["first_yin_amplitude"], "min_amplitude": 0.07}

    baseline = await _report(
        samples, params, strategy.build_factor_params(params, default_factor_params(ids)), paths
    )
    relaxed = await _report(samples, params, tuned, paths)

    assert baseline.segment(SEGMENT_ALL).path(PATH_S2).n == 180  # type: ignore[attr-defined]
    assert baseline.segment(SEGMENT_ALL).path(PATH_S4).n == 165  # type: ignore[attr-defined]

    # 放宽振幅门槛后两路样本数均上升，触发数随之上升
    assert relaxed.segment(SEGMENT_ALL).path(PATH_S2).n == 195  # type: ignore[attr-defined]
    assert relaxed.segment(SEGMENT_ALL).path(PATH_S4).n == 185  # type: ignore[attr-defined]
    assert relaxed.trigger_count > baseline.trigger_count  # type: ignore[attr-defined]


def test_hard_gate_thresholds_come_from_factor_params() -> None:
    """两路硬门槛的阈值参数均来自**因子注册表**声明的参数键（非策略硬编码）。"""
    from app.factors.base import get_factor

    expected = {
        "first_yin_shape": "gate_shape",
        "first_yin_amplitude": "min_amplitude",
        "next_day_open_pct": "panic_open",
        "next_day_vol_vs_first_yin": "gate_ratio",
    }
    for factor_id, key in expected.items():
        spec = next(item for item in get_factor(factor_id)().params_schema if item.key == key)
        assert spec.default is not None

    # 缺失阈值参数时显式报错（不会静默回退到硬编码常量）
    with pytest.raises(KeyError):
        param_value({}, "min_amplitude")


# ============================================================ 加分项加码 / 互斥


async def test_bonus_scaling_changes_s4_positions_but_not_s2(
    samples: list[DragonSample],
    strategy: DragonStrategy,
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
    paths: list[PathSpec],
) -> None:
    """S4 按加分项加码（仓位 > 基础仓）；S2 不加码（恒为基础仓）。"""
    from app.engine.portfolio import evaluate_path

    s2 = next(path for path in paths if path.path_id == PATH_S2)
    s4 = next(path for path in paths if path.path_id == PATH_S4)
    base_position = float(strategy.param(params, "base_position"))

    s4_scaled = 0
    s2_positions: set[float] = set()
    for sample in samples:
        ctx = sample.factor_context()
        evaluation = evaluate_path(s4, sample, ctx, factor_params)
        if evaluation.matched and evaluation.bonus_score >= 1:
            assert evaluation.position > base_position
            s4_scaled += 1
        s2_eval = evaluate_path(s2, sample, ctx, factor_params)
        if s2_eval.matched:
            s2_positions.add(round(s2_eval.position, 10))

    assert s4_scaled > 0, "S4 应有按分加码的样本"
    assert s2_positions == {round(base_position, 10)}, "S2 不得按分加码"


def test_s4_bonus_items_4_and_6_are_mutually_exclusive(
    samples: list[DragonSample],
    strategy: DragonStrategy,
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
    paths: list[PathSpec],
) -> None:
    """S4 加分项 ④（低点≥90分）与 ⑥（30~90分）互斥，同一样本不可能同时满足。"""
    from app.engine.portfolio import evaluate_path

    s4 = next(path for path in paths if path.path_id == PATH_S4)
    both = 0
    for sample in samples:
        evaluation = evaluate_path(s4, sample, sample.factor_context(), factor_params)
        satisfied = {hit.label for hit in evaluation.bonus if hit.satisfied}
        late = any("≥90分" in label for label in satisfied)
        middle = any("30~90分" in label for label in satisfied)
        if late and middle:
            both += 1
    assert both == 0


# ============================================================ 止损口径（仅可售日）


def _made_sample(
    *,
    px_t: tuple[float, ...],
    px_t1: tuple[float, ...],
    shape: str = "尾盘跳水",
    d_amp: float = 0.10,
    t_open_pct: float = -0.05,
) -> DragonSample:
    """构造一条命中 S2 硬门槛的样本（用于止损口径测试）。"""
    return DragonSample(
        code="600001.SH",
        name="构造股",
        D=date(2026, 1, 7),
        T=date(2026, 1, 8),
        T1=date(2026, 1, 9),
        T2=date(2026, 1, 12),
        shape_label=shape,
        d_amp_pct=d_amp,
        t_vol_vs_d=0.5,
        pre_close=12.1,
        t=DayMetrics(open=10.0, open_pct=t_open_pct),
        t1=DayMetrics(open=10.0, open_pct=0.0),
        mp_D=MinuteMetrics(n=240, low_time_i=200, close_pos=0.0),
        px_D=(12.6,) * 200 + (11.5,) * 40,
        px_T=px_t,
        px_T1=px_t1,
        px_T2=(10.0,) * 240,
    )


def test_stop_loss_only_applies_on_sellable_day(
    paths: list[PathSpec], factor_params: dict[str, dict[str, object]]
) -> None:
    """S2 在 D+1 开盘买入、可卖日为 D+2；D+1 全天暴跌 5% 也**不触发**止损。"""
    from app.engine.portfolio import evaluate_path

    s2 = next(path for path in paths if path.path_id == PATH_S2)
    sample = _made_sample(px_t=(9.5,) * 240, px_t1=(10.0,) * 240)

    # 可卖日序列 = D+2（px_T1），买入当日（px_T）不在其中
    assert s2.sellable_series(sample)[0] == sample.px_T1

    evaluation = evaluate_path(s2, sample, sample.factor_context(), factor_params)
    assert evaluation.matched
    assert evaluation.sell is not None
    assert evaluation.sell.exit_reason == EXIT_CLOSE  # 未触发止损
    assert evaluation.sell.return_pct == pytest.approx(0.0)


def test_sellable_day_stop_loss_and_one_word_limit_down_fallback(
    paths: list[PathSpec], factor_params: dict[str, dict[str, object]]
) -> None:
    """可卖日一字跌停：回测按收盘价成交（乐观假设）；设止损则按 -3% 成交。"""
    from app.engine.portfolio import evaluate_path

    s2 = next(path for path in paths if path.path_id == PATH_S2)
    sample = _made_sample(px_t=(10.0,) * 240, px_t1=(9.0,) * 240)  # 可卖日 -10%

    with_stop = evaluate_path(s2, sample, sample.factor_context(), factor_params)
    assert with_stop.sell is not None
    assert with_stop.sell.exit_reason == EXIT_STOP_LOSS
    assert with_stop.sell.return_pct == pytest.approx(-0.03)

    no_stop = evaluate_path(
        s2,
        sample,
        sample.factor_context(),
        factor_params,
        sell_config=SellRuleConfig(stop_loss=None),
    )
    assert no_stop.sell is not None
    assert no_stop.sell.exit_reason == EXIT_CLOSE
    assert no_stop.sell.return_pct == pytest.approx(-0.10)


# ============================================================ 结构化建议（可解释）


def test_advice_is_explainable(
    samples: list[DragonSample],
    strategy: DragonStrategy,
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
) -> None:
    """建议含：路次 / 命中硬门槛明细 / 加分项清单 / 建议仓位 / 止损价 / 卖出时点 / 字段快照。"""
    target = next(
        sample
        for sample in samples
        if sample.shape_label == "尾盘跳水"
        and (sample.d_amp_pct or 0) >= 0.08
        and (sample.t.open_pct or 0) <= -0.03
    )

    advice = strategy.advise(target, factor_params=factor_params, params=params)
    assert advice is not None
    assert advice.path_id == PATH_S2
    assert advice.path_label == "高位跳水型"
    assert all(hit.passed for hit in advice.gates)
    assert advice.gates[1].detail.startswith("d_amp_pct=")
    assert advice.bonus_score == len([hit for hit in advice.bonus if hit.satisfied])
    assert advice.position == pytest.approx(0.20)
    assert advice.stop_loss_price == pytest.approx(advice.buy_price * 0.97)
    assert "收盘了结" in advice.sell_timing

    payload = advice.to_payload()
    assert set(payload) >= {
        "path_id",
        "path_label",
        "gates",
        "bonus",
        "bonus_score",
        "position",
        "stop_loss_price",
        "sell_timing",
        "field_snapshot",
    }
    assert payload["field_snapshot"]["code"] == target.code


# ============================================================ 过拟合对照（readme §11）


def _band(ctx: object, metric: str, p: dict[str, object], low: str, high: str) -> bool:
    """``low <= metric < high``（阈值取自因子参数）。"""
    value = number(ctx.metric(metric))  # type: ignore[attr-defined]
    return value is not None and float(param_value(p, low)) <= value < float(param_value(p, high))


def _below(ctx: object, metric: str, p: dict[str, object], key: str) -> bool:
    """``metric < threshold``（阈值取自因子参数）。"""
    value = number(ctx.metric(metric))  # type: ignore[attr-defined]
    return value is not None and value < float(param_value(p, key))


def _at_most(ctx: object, metric: str, p: dict[str, object], key: str) -> bool:
    """``metric <= threshold``（阈值取自因子参数）。"""
    value = number(ctx.metric(metric))  # type: ignore[attr-defined]
    return value is not None and value <= float(param_value(p, key))


def _overfit_path() -> PathSpec:
    """readme §11「C 段贪心」D+1 组合（窗口内过拟合链，用作对照）。"""
    return PathSpec(
        path_id="OVERFIT",
        label="C段贪心(过拟合对照)",
        priority=1,
        buy_day_field="T",
        buy_metrics_field="T",
        sellable_day_fields=("T1",),
        base_position=0.20,
        max_position_factor=1.0,
        scale_by_bonus=False,
        gates=(
            GateSpec(
                factor_id="vol_vs_wave_peak",
                label="首阴量/连板峰值 0.8~1.0",
                test=lambda ctx, p: _band(ctx, "vol_vs_wave_peak", p, "s1", "s2"),
                describe=lambda ctx, p: "vol_vs_wave_peak",
            ),
            GateSpec(
                factor_id="first_yin_amplitude",
                label="首阴振幅≥硬门槛",
                test=lambda ctx, p: (
                    number(ctx.metric("d_amp_pct")) is not None
                    and number(ctx.metric("d_amp_pct")) >= float(param_value(p, "min_amplitude"))
                ),  # type: ignore[operator]
                describe=lambda ctx, p: "d_amp_pct",
            ),
            GateSpec(
                factor_id="vol_vs_wave_mean",
                label="首阴量/连板均量<1.5",
                test=lambda ctx, p: _below(ctx, "vol_vs_wave_mean", p, "s1"),
                describe=lambda ctx, p: "vol_vs_wave_mean",
            ),
            GateSpec(
                factor_id="next_day_open_pct",
                label="次日开盘≤低开硬门槛",
                test=lambda ctx, p: _at_most(ctx, "t.open_pct", p, "panic_open"),
                describe=lambda ctx, p: "t.open_pct",
            ),
        ),
        bonus=(),
    )


async def test_overfit_control_variant_is_negative_in_a_and_b(
    samples: list[DragonSample],
    strategy: DragonStrategy,
    params: dict[str, object],
) -> None:
    """过拟合对照（readme §11）：C 段贪心链在 B 段为负，证明其不可复现。

    复现 readme §11 数值：n=48 / A +2.99% / B **-1.03%** / C +5.64%（止损档 -4%）。
    """
    ids = (*strategy.factor_param_ids(), "vol_vs_wave_peak", "vol_vs_wave_mean")
    resolved = strategy.build_factor_params(params, default_factor_params(ids))
    report = await _report(samples, params, resolved, [_overfit_path()], stop_loss=-0.04)

    assert report.segment(SEGMENT_ALL).path("OVERFIT").n == 48  # type: ignore[attr-defined]
    _assert_pct(report.segment(SEGMENT_A).path("OVERFIT").mean_return, 2.99)  # type: ignore[attr-defined]
    assert report.segment(SEGMENT_B).path("OVERFIT").mean_return < 0  # type: ignore[attr-defined]
    _assert_pct(report.segment(SEGMENT_B).path("OVERFIT").mean_return, -1.03)  # type: ignore[attr-defined]
    _assert_pct(report.segment(SEGMENT_C).path("OVERFIT").mean_return, 5.64)  # type: ignore[attr-defined]


async def test_variants_are_reported(
    samples: list[DragonSample],
    strategy: DragonStrategy,
    params: dict[str, object],
    factor_params: dict[str, dict[str, object]],
    paths: list[PathSpec],
) -> None:
    """``variants`` 的对照结果被写入报告（如窄口径 / 过拟合链）。"""
    ids = (*strategy.factor_param_ids(), "vol_vs_wave_peak", "vol_vs_wave_mean")
    resolved = strategy.build_factor_params(params, default_factor_params(ids))
    report = await _report(
        samples,
        params,
        resolved,
        paths,
        variants={"过拟合链": [_overfit_path()]},
    )
    assert "过拟合链" in report.variants  # type: ignore[attr-defined]
    assert report.variants["过拟合链"][SEGMENT_B].cumulative_return < 0  # type: ignore[attr-defined]


# ============================================================ 生命周期钩子（POOL / INTRADAY）


def _daily_bar(
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
    """构造一根合成日线。"""
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


def _minute_bars(code: str, day: date, prices: Sequence[float]) -> list[MinuteBar]:
    """构造 240 点合成分时。"""
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


async def test_confirm_intraday_hook_emits_and_persists_advice() -> None:
    """``confirm_intraday`` 钩子：从库内合成数据建样本 → 判定 → 落 ``advice_reports``。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repos = Repositories.build(session)
        code = "600001.SH"
        session.add(Stock(code=code, name="测试股", market="SH", board="主板", is_st=False))
        session.add_all(
            [
                _daily_bar(
                    code,
                    date(2026, 1, 5),
                    open_px=10.5,
                    high=11.0,
                    low=10.4,
                    close=11.0,
                    pre_close=10.0,
                    volume=1_000,
                ),
                _daily_bar(
                    code,
                    date(2026, 1, 6),
                    open_px=12.1,
                    high=12.1,
                    low=12.1,
                    close=12.1,
                    pre_close=11.0,
                    volume=2_000,
                ),
                # 首阴 D：尾盘跳水形态（见下方分时）+ 振幅 10.7%
                _daily_bar(
                    code,
                    date(2026, 1, 7),
                    open_px=12.6,
                    high=12.8,
                    low=11.5,
                    close=11.6,
                    pre_close=12.1,
                    volume=3_000,
                ),
                # T（D+1）低开 -5% → 命中 S2 硬门槛
                _daily_bar(
                    code,
                    date(2026, 1, 8),
                    open_px=11.0,
                    high=11.8,
                    low=10.9,
                    close=11.7,
                    pre_close=11.6,
                    volume=900,
                ),
                _daily_bar(
                    code,
                    date(2026, 1, 9),
                    open_px=11.6,
                    high=12.0,
                    low=11.4,
                    close=11.9,
                    pre_close=11.7,
                    volume=1_100,
                ),
            ]
        )
        session.add_all(_minute_bars(code, date(2026, 1, 7), [12.6] * 200 + [11.5] * 40))
        session.add_all(_minute_bars(code, date(2026, 1, 8), [11.0] * 240))
        # S2 的可卖日 = D+2（T1），需有分时序列方可撮合
        session.add_all(_minute_bars(code, date(2026, 1, 9), [11.6] * 240))
        await session.commit()

        strategy = DragonStrategy()
        ctx = StrategyContext(
            strategy_id="dragon",
            trade_date=date(2026, 1, 8),
            repos=repos,
            clock=lambda: datetime(2026, 1, 8, 9, 25, tzinfo=UTC),
        )

        pool = await strategy.build_pool(ctx)
        assert [item["code"] for item in pool["candidates"]] == [code]

        # 盘后建池落库（快照语义）：候选可在 dragon_pool 表回查，供量化选股页展示
        pool_rows = await repos.dragon_pool.get_by_date(date(2026, 1, 8))
        assert [row.code for row in pool_rows] == [code]
        assert pool_rows[0].d_date == date(2026, 1, 7)
        assert pool_rows[0].strategy_id == "dragon"

        # 重跑整体替换，不产生重复行
        await strategy.build_pool(ctx)
        assert len(await repos.dragon_pool.get_by_date(date(2026, 1, 8))) == 1

        result = await strategy.confirm_intraday(ctx)
        assert len(result["advices"]) == 1
        advice = result["advices"][0]
        assert advice["path_id"] == PATH_S2
        assert advice["sell_timing"]
        assert advice["field_snapshot"]["D"] == "2026-01-07"

        reports = await repos.advice_reports.get_by_date(date(2026, 1, 7), kind=KIND_ADVICE)
        assert len(reports) == 1
        assert reports[0].strategy_id == "dragon"
        assert reports[0].payload["path_id"] == PATH_S2
    await engine.dispose()
