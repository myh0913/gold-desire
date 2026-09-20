"""组合风控与模拟（readme §7）：多路并行、同票去重、总仓位 80% 等比压缩。

复现参考实现 ``dr3_step.py::sim`` 的口径：

- **按样本去重**（readme §7.3）：同一票同一 ``D`` 日多路命中时，只取**优先级最高**的一路
  （``S2 > S4``），**不叠加仓位**；故每条样本**最多计一次触发**——这正是组合触发数为
  **311**（而非两路简单相加 345）的原因。
- **单笔仓位**：S2 固定基础仓（不加码）；S4 按加分项加码
  ``min(基础仓 × (1 + 0.25 × 加分项数), 基础仓 × 1.5)``（readme §7.1 / §14.5）。
- **同日总仓位上限 80%**：超限时**等比例压缩**（``k = cap / total``）。等比压缩后当日总仓位
  恰为 80%，不存在「压缩后仍超过」的情形（readme §7.2），故无需降级兜底规则。
- 段内按**买入日历日**分组结算（S2 记 ``T``、S4 记 ``T1``），累计收益为日收益连乘。

另暴露 :class:`GateSpec` / :class:`BonusSpec` / :class:`PathSpec`——策略插件用它们声明路径、
硬门槛与加分项；阈值一律来自**因子参数**（由调用方解析后经 ``factor_params`` 注入），
故引擎与插件都不硬编码阈值。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING, Any

from app.engine.sell_rules import (
    DEFAULT_HOLD_SELLABLE_DAYS,
    DEFAULT_STOP_LOSS,
    SellRuleConfig,
    SellRuleResult,
)
from app.engine.sell_rules import (
    evaluate as evaluate_sell,
)

if TYPE_CHECKING:
    from app.engine.dragon_samples import DragonSample
    from app.factors.base import FactorContext

__all__ = [
    "DEFAULT_BONUS_STEP",
    "DEFAULT_CAP",
    "BonusHit",
    "BonusSpec",
    "FactorParams",
    "GateHit",
    "GateSpec",
    "PathEvaluation",
    "PathSpec",
    "PortfolioResult",
    "PortfolioStats",
    "evaluate_path",
    "sim",
]

#: 同日总仓位上限（readme §7.2）。
DEFAULT_CAP = 0.80
#: S4 加分项加码步长（readme §7.1）。
DEFAULT_BONUS_STEP = 0.25

#: 因子参数视图：``{factor_id: {参数键: 值}}``（阈值来源，禁止硬编码）。
FactorParams = Mapping[str, Mapping[str, Any]]

#: 单门槛判定：读因子上下文与**该因子的已解析参数**。
GateTest = Callable[["FactorContext", Mapping[str, Any]], bool]
#: 门槛说明：人类可读的「字段=实际值 与 阈值」描述（供建议可解释）。
GateDescribe = Callable[["FactorContext", Mapping[str, Any]], str]
#: 加分项判定：读因子上下文与**该因子的已解析参数**。
BonusTest = Callable[["FactorContext", Mapping[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class GateSpec:
    """一条硬门槛声明（全部 AND 命中方可开仓）。

    Attributes:
        factor_id: 阈值来源因子标识（参数从该因子的已解析参数读取）。
        label: 门槛标签（如「首阴振幅≥8%」）。
        test: 判定函数。
        describe: 字段快照描述函数（命中明细）。
    """

    factor_id: str
    label: str
    test: GateTest
    describe: GateDescribe


@dataclass(frozen=True, slots=True)
class BonusSpec:
    """一条加分项声明（S4 用于加码，S2 仅展示）。

    Attributes:
        factor_id: 阈值来源因子标识。
        label: 加分项标签。
        test: 判定函数。
    """

    factor_id: str
    label: str
    test: BonusTest


@dataclass(frozen=True, slots=True)
class PathSpec:
    """一条路的完整声明（买点、卖出、仓位与门槛）。

    Attributes:
        path_id: 路标识（``"S2"`` / ``"S4"``）。
        label: 路标签（如「高位跳水型」）。
        priority: 去重优先级（数值越小越优先，readme §7.3 ``S2 > S4``）。
        buy_day_field: 买入日历日字段（``"T"`` / ``"T1"``）。
        buy_metrics_field: 买入价所在的全天字段对象（``"T"`` / ``"T1"``）。
        sellable_day_fields: 可卖日字段（按顺序，如 ``("T1", "T2")``）。
        base_position: 基础仓位（readme §7.1）。
        max_position_factor: 单路上限倍数（readme §7.1，S4 为 1.5）。
        scale_by_bonus: 是否按加分项加码（S2 ``False`` / S4 ``True``）。
        gates: 硬门槛。
        bonus: 加分项。
    """

    path_id: str
    label: str
    priority: int
    buy_day_field: str
    buy_metrics_field: str
    sellable_day_fields: tuple[str, ...]
    base_position: float
    max_position_factor: float
    scale_by_bonus: bool
    gates: tuple[GateSpec, ...]
    bonus: tuple[BonusSpec, ...]

    def buy_day(self, sample: DragonSample) -> date | None:
        """取买入日历日。"""
        return sample.buy_day(self.buy_day_field)

    def buy_price(self, sample: DragonSample) -> float | None:
        """取买入价（开盘价）。"""
        return sample.day_metrics(self.buy_metrics_field).open

    def sellable_series(self, sample: DragonSample) -> tuple[tuple[float, ...], ...]:
        """取可卖日分钟价序列（止损仅在可卖日生效）。"""
        return tuple(sample.minute_prices(field) for field in self.sellable_day_fields)

    def position_for(self, bonus_score: int, bonus_step: float = DEFAULT_BONUS_STEP) -> float:
        """按加分项得分计算单笔仓位（不加码路直接返回基础仓）。"""
        if not self.scale_by_bonus:
            return self.base_position
        scaled = self.base_position * (1 + bonus_step * bonus_score)
        return min(scaled, self.base_position * self.max_position_factor)

    def without_bonus_scaling(self) -> PathSpec:
        """返回关闭加码的副本（用于「基础版 vs 推荐版」对照，readme §7.4）。"""
        return replace(self, scale_by_bonus=False)


@dataclass(frozen=True, slots=True)
class GateHit:
    """单条硬门槛的判定结果（含字段快照描述）。"""

    factor_id: str
    label: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class BonusHit:
    """单条加分项的判定结果。"""

    factor_id: str
    label: str
    satisfied: bool


@dataclass(frozen=True, slots=True)
class PathEvaluation:
    """单条样本在某条路上的完整判定（含建议明细）。"""

    path_id: str
    label: str
    gates: tuple[GateHit, ...]
    bonus: tuple[BonusHit, ...]
    matched: bool
    bonus_score: int
    position: float
    buy_day: date | None
    sell: SellRuleResult | None

    @property
    def gate_details(self) -> tuple[GateHit, ...]:
        """命中的硬门槛明细。"""
        return tuple(hit for hit in self.gates if hit.passed)

    @property
    def bonus_labels(self) -> tuple[str, ...]:
        """满足的加分项标签。"""
        return tuple(hit.label for hit in self.bonus if hit.satisfied)


def evaluate_path(
    path: PathSpec,
    sample: DragonSample,
    ctx: FactorContext,
    factor_params: FactorParams,
    *,
    bonus_step: float = DEFAULT_BONUS_STEP,
    sell_config: SellRuleConfig | None = None,
) -> PathEvaluation:
    """判定一条样本在某条路上是否命中，并给出建议仓位与卖出结果。

    Args:
        path: 路声明。
        sample: 样本。
        ctx: 由 ``sample.factor_context()`` 构造的因子上下文。
        factor_params: ``{factor_id: 已解析参数}``——**阈值唯一来源**。
        bonus_step: 加分项加码步长。
        sell_config: 卖出规则参数；``None`` 用默认（无止盈 + 3% 止损 + 持 1 日）。
    """
    config = sell_config or SellRuleConfig(
        take_profit=None, stop_loss=DEFAULT_STOP_LOSS, hold_sellable_days=DEFAULT_HOLD_SELLABLE_DAYS
    )
    gates = tuple(
        GateHit(
            factor_id=spec.factor_id,
            label=spec.label,
            passed=spec.test(ctx, factor_params.get(spec.factor_id, {})),
            detail=spec.describe(ctx, factor_params.get(spec.factor_id, {})),
        )
        for spec in path.gates
    )
    matched = all(hit.passed for hit in gates)
    bonus = tuple(
        BonusHit(
            factor_id=spec.factor_id,
            label=spec.label,
            satisfied=spec.test(ctx, factor_params.get(spec.factor_id, {})),
        )
        for spec in path.bonus
    )
    score = sum(1 for hit in bonus if hit.satisfied)
    position = path.position_for(score, bonus_step)

    buy_day: date | None = None
    sell: SellRuleResult | None = None
    if matched:
        buy_day = path.buy_day(sample)
        sell = evaluate_sell(
            path.buy_price(sample),
            path.sellable_series(sample),
            take_profit=config.take_profit,
            stop_loss=config.stop_loss,
            hold_sellable_days=config.hold_sellable_days,
        )
    return PathEvaluation(
        path_id=path.path_id,
        label=path.label,
        gates=gates,
        bonus=bonus,
        matched=matched,
        bonus_score=score,
        position=position,
        buy_day=buy_day,
        sell=sell,
    )


@dataclass(frozen=True, slots=True)
class PortfolioStats:
    """组合模拟统计。

    Attributes:
        trigger_count: 触发次数（按样本去重后，每条样本最多一次）。
        trading_days: 有持仓的交易日数。
        cumulative_return: 段内累计收益（日收益连乘，小数口径）。
        avg_daily_return: 日均收益。
        daily_win_rate: 日胜率（日收益 > 0 占比）。
        max_drawdown: 最大回撤（负数）。
        worst_day: 最差单日收益。
        avg_usage: 平均资金占用。
        return_on_usage: 日均收益 / 平均占用。
    """

    trigger_count: int
    trading_days: int
    cumulative_return: float
    avg_daily_return: float
    daily_win_rate: float
    max_drawdown: float
    worst_day: float
    avg_usage: float
    return_on_usage: float

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "trigger_count": self.trigger_count,
            "trading_days": self.trading_days,
            "cumulative_return": self.cumulative_return,
            "avg_daily_return": self.avg_daily_return,
            "daily_win_rate": self.daily_win_rate,
            "max_drawdown": self.max_drawdown,
            "worst_day": self.worst_day,
            "avg_usage": self.avg_usage,
            "return_on_usage": self.return_on_usage,
        }


#: 空组合统计（无触发时的兜底）。
EMPTY_STATS = PortfolioStats(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    """组合模拟结果：统计 + 逐日收益（供分项审计）。"""

    stats: PortfolioStats
    daily_returns: tuple[tuple[date, float], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（逐日收益转为 ``{日期: 收益}``）。"""
        return {
            "stats": self.stats.to_dict(),
            "daily_returns": {day.isoformat(): value for day, value in self.daily_returns},
        }


def sim(
    paths: Sequence[PathSpec],
    samples: Sequence[DragonSample],
    *,
    factor_params: FactorParams,
    cap: float = DEFAULT_CAP,
    bonus_step: float = DEFAULT_BONUS_STEP,
    sell_config: SellRuleConfig | None = None,
    include: Callable[[DragonSample], bool] | None = None,
) -> PortfolioResult:
    """按组合规则模拟一组路径在给定样本上的表现。

    Args:
        paths: 路声明（按 ``priority`` 升序去重）。
        samples: 样本序列。
        factor_params: ``{factor_id: 已解析参数}``。
        cap: 同日总仓位上限；默认 0.80。
        bonus_step: 加分项加码步长；默认 0.25。
        sell_config: 卖出规则参数；``None`` 用默认。
        include: 段过滤（如按 ``D`` 落在 A/B/C）；``None`` 表示全量。

    Returns:
        :class:`PortfolioResult`（无触发时统计全 0）。
    """
    ordered_paths = sorted(paths, key=lambda path: path.priority)
    ordered_samples = sorted(samples, key=lambda sample: (sample.D, sample.code))

    by_day: dict[date, list[tuple[float, float]]] = {}
    trigger_count = 0
    for sample in ordered_samples:
        if include is not None and not include(sample):
            continue
        ctx = sample.factor_context()
        chosen: PathEvaluation | None = None
        for path in ordered_paths:
            evaluation = evaluate_path(
                path, sample, ctx, factor_params, bonus_step=bonus_step, sell_config=sell_config
            )
            if (
                evaluation.matched
                and evaluation.sell is not None
                and evaluation.buy_day is not None
            ):
                chosen = evaluation
                break
        if chosen is None or chosen.sell is None or chosen.buy_day is None:
            continue
        by_day.setdefault(chosen.buy_day, []).append((chosen.position, chosen.sell.return_pct))
        trigger_count += 1

    daily: dict[date, float] = {}
    usages: list[float] = []
    for day, items in by_day.items():
        total = sum(position for position, _ in items)
        scale = cap / total if total > cap else 1.0
        daily[day] = sum(position * scale * ret for position, ret in items)
        usages.append(total * scale)

    days = sorted(daily)
    if not days:
        return PortfolioResult(stats=EMPTY_STATS)

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for day in days:
        equity *= 1 + daily[day]
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1)

    values = [daily[day] for day in days]
    avg = sum(values) / len(values)
    usage = sum(usages) / len(usages) if usages else 0.0
    stats = PortfolioStats(
        trigger_count=trigger_count,
        trading_days=len(days),
        cumulative_return=equity - 1,
        avg_daily_return=avg,
        daily_win_rate=sum(1 for value in values if value > 0) / len(values),
        max_drawdown=max_drawdown,
        worst_day=min(values),
        avg_usage=usage,
        return_on_usage=avg / usage if usage else 0.0,
    )
    return PortfolioResult(stats=stats, daily_returns=tuple((day, daily[day]) for day in days))
