"""回测引擎：按 A/B/C 三段输出组合表现（readme §1.5 / §7.4）。

设计要求（spec「龙回头策略实现」）：

- **幂等**：同输入（样本 + 参数 + 路径）⇒ 输出**逐字节一致**；报告内不含任何时间戳。
- **禁止只报全量**：始终按 A/B/C 三段分别输出，并附全量汇总。
- **口径对照**：附带「推荐版（S4 按分加码）vs 基础版（不加码）」对照（readme §7.4）；
  并可传入 ``variants``（如窄口径 / C 段贪心过拟合链）做对照检验。
- **持久化**：经 :class:`~app.repositories.derived.BacktestRunRepository` 落 ``backtest_runs``。

阈值一律来自 ``factor_params``（由 :func:`resolve_factor_params` 从**因子注册表**解析），
引擎与策略都不硬编码阈值。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from app.engine.portfolio import (
    DEFAULT_BONUS_STEP,
    DEFAULT_CAP,
    PathSpec,
    PortfolioStats,
    evaluate_path,
    sim,
)
from app.engine.segments import (
    SEGMENT_ALL,
    SEGMENT_ORDER,
    Segments,
    split_segments,
)
from app.engine.sell_rules import (
    DEFAULT_HOLD_SELLABLE_DAYS,
    DEFAULT_STOP_LOSS,
    SellRuleConfig,
)
from app.factors.base import FactorRegistryError
from app.factors.registry import resolve_params as _registry_resolve_params

if TYPE_CHECKING:
    from app.engine.dragon_samples import DragonSample

__all__ = [
    "BacktestReport",
    "PathSegmentStats",
    "SegmentReport",
    "default_factor_params",
    "resolve_factor_params",
    "run_backtest",
]


def _mean(values: Sequence[float]) -> float:
    """算术平均；空序列返回 0.0。"""
    return sum(values) / len(values) if values else 0.0


def _is_factor(factor_id: str) -> bool:
    """判定标识是否为**已注册因子**（策略命名空间等非因子标识返回 ``False``）。"""
    from app.factors.base import get_factor

    try:
        get_factor(factor_id)
    except FactorRegistryError:
        return False
    return True


def _win_rate(values: Sequence[float]) -> float:
    """胜率（> 0 占比）；空序列返回 0.0。"""
    return sum(1 for value in values if value > 0) / len(values) if values else 0.0


async def resolve_factor_params(
    factor_ids: Sequence[str], repos: Any | None = None
) -> dict[str, dict[str, Any]]:
    """从**因子注册表**解析一组因子的生效参数（覆盖 > active 配置 > 代码默认）。

    Args:
        factor_ids: 需要的因子标识（去重）。
        repos: 仓储容器；``None`` 时仅用代码默认（测试可脱离 DB）。

    Returns:
        ``{factor_id: 已解析参数}``——策略阈值唯一来源。
    """
    out: dict[str, dict[str, Any]] = {}
    for factor_id in dict.fromkeys(factor_ids):
        resolved = await _registry_resolve_params(factor_id, repos)
        out[factor_id] = dict(resolved.params)
    return out


def default_factor_params(factor_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """同步取一组因子的**代码默认**参数（供轻量/同步调用，如建议展示）。"""
    from app.factors.base import get_factor

    return {
        factor_id: get_factor(factor_id)().default_params()
        for factor_id in dict.fromkeys(factor_ids)
    }


@dataclass(frozen=True, slots=True)
class PathSegmentStats:
    """单路在某段的统计（对应参考实现 ``eval_preds``）。"""

    path_id: str
    label: str
    n: int
    mean_return: float
    win_rate: float

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "path_id": self.path_id,
            "label": self.label,
            "n": self.n,
            "mean_return": self.mean_return,
            "win_rate": self.win_rate,
        }


@dataclass(frozen=True, slots=True)
class SegmentReport:
    """单段结果：各路统计 + 组合统计。"""

    name: str
    sample_count: int
    paths: tuple[PathSegmentStats, ...]
    portfolio: PortfolioStats

    def path(self, path_id: str) -> PathSegmentStats | None:
        """取某路在该段的统计。"""
        for item in self.paths:
            if item.path_id == path_id:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "name": self.name,
            "sample_count": self.sample_count,
            "paths": [item.to_dict() for item in self.paths],
            "portfolio": self.portfolio.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """回测报告（含 A/B/C 三段，绝不只报全量）。

    Attributes:
        strategy_id: 策略标识。
        start: 样本基准日起（含）。
        end: 样本基准日止（含）。
        params: 策略参数快照（provenance）。
        path_ids: 参与组合的路径标识（按优先级）。
        segments: 按展示顺序（A/B/C/全量）的分段结果。
        caliber: 口径对照 ``{配置名: {段名: PortfolioStats}}``
            （``推荐版`` = S4 加码；``基础版`` = 不加码）。
        variants: 额外对照 ``{方案名: {段名: PortfolioStats}}``（如窄口径 / 过拟合链）。
        trigger_count: 全量触发次数（按样本去重后）。
    """

    strategy_id: str
    start: date
    end: date
    params: dict[str, Any]
    path_ids: tuple[str, ...]
    segments: tuple[SegmentReport, ...]
    caliber: dict[str, dict[str, PortfolioStats]]
    variants: dict[str, dict[str, PortfolioStats]]
    trigger_count: int

    def segment(self, name: str) -> SegmentReport | None:
        """按段名取结果（``"A"`` / ``"B"`` / ``"C"`` / ``"全量"``）。"""
        for item in self.segments:
            if item.name == name:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 可存储的字典（确定性：无时间戳）。"""
        return {
            "strategy_id": self.strategy_id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "params": dict(self.params),
            "path_ids": list(self.path_ids),
            "trigger_count": self.trigger_count,
            "segments": [item.to_dict() for item in self.segments],
            "caliber": {
                name: {seg: stats.to_dict() for seg, stats in per_segment.items()}
                for name, per_segment in self.caliber.items()
            },
            "variants": {
                name: {seg: stats.to_dict() for seg, stats in per_segment.items()}
                for name, per_segment in self.variants.items()
            },
        }


def _segment_filters(segments: Segments) -> dict[str, Callable[[DragonSample], bool]]:
    """构造各段过滤谓词（按基准日 ``D`` 归属，与参考实现一致）。"""
    cut_ab, cut_bc = segments.cuts
    return {
        "A": lambda sample: cut_ab > sample.D,
        "B": lambda sample: cut_ab <= sample.D < cut_bc,
        "C": lambda sample: cut_bc <= sample.D,
        SEGMENT_ALL: lambda sample: True,
    }


def _path_segment_stats(
    path: PathSpec,
    samples: Sequence[DragonSample],
    factor_params: Mapping[str, Mapping[str, Any]],
    sell_config: SellRuleConfig,
) -> PathSegmentStats:
    """计算单路在某段的样本数与期望（对应参考实现 ``eval_preds``）。"""
    returns: list[float] = []
    for sample in samples:
        evaluation = evaluate_path(
            path, sample, sample.factor_context(), factor_params, sell_config=sell_config
        )
        if evaluation.matched and evaluation.sell is not None:
            returns.append(evaluation.sell.return_pct)
    return PathSegmentStats(
        path_id=path.path_id,
        label=path.label,
        n=len(returns),
        mean_return=_mean(returns),
        win_rate=_win_rate(returns),
    )


def _portfolio_by_segment(
    paths: Sequence[PathSpec],
    samples: Sequence[DragonSample],
    filters: Mapping[str, Callable[[DragonSample], bool]],
    factor_params: Mapping[str, Mapping[str, Any]],
    *,
    cap: float,
    bonus_step: float,
    sell_config: SellRuleConfig,
) -> dict[str, PortfolioStats]:
    """按段计算组合统计。"""
    return {
        name: sim(
            paths,
            samples,
            factor_params=factor_params,
            cap=cap,
            bonus_step=bonus_step,
            sell_config=sell_config,
            include=predicate,
        ).stats
        for name, predicate in filters.items()
    }


async def run_backtest(
    samples: Sequence[DragonSample],
    params: Mapping[str, Any],
    *,
    paths: Sequence[PathSpec],
    factor_params: Mapping[str, Mapping[str, Any]] | None = None,
    cap: float = DEFAULT_CAP,
    bonus_step: float = DEFAULT_BONUS_STEP,
    stop_loss: float = DEFAULT_STOP_LOSS,
    hold_sellable_days: int = DEFAULT_HOLD_SELLABLE_DAYS,
    cuts: tuple[date, date] | None = None,
    strategy_id: str = "dragon",
    variants: Mapping[str, Sequence[PathSpec]] | None = None,
    repos: Any | None = None,
    run_id: str | None = None,
) -> BacktestReport:
    """执行回测并返回分段报告（幂等）。

    Args:
        samples: 样本序列。
        params: 策略参数快照（provenance；阈值不从此处取，见 ``factor_params``）。
        paths: 路径声明（策略插件提供）。
        factor_params: ``{factor_id: 已解析参数}``；``None`` 时从因子注册表解析代码默认。
        cap: 同日总仓位上限；默认 0.80。
        bonus_step: 加分项加码步长；默认 0.25。
        stop_loss: 止损档；默认 -0.03。
        hold_sellable_days: 持有可卖日数；默认 1。
        cuts: 显式 A/B/C 切点；``None`` 按 readme §1.5 口径动态计算。
        strategy_id: 策略标识（落库用）。
        variants: 额外对照方案 ``{方案名: 路径集}``（如窄口径 / 过拟合链）。
        repos: 仓储容器；非 ``None`` 时把报告落 ``backtest_runs``。
        run_id: 回测任务 ID；``repos`` 非 ``None`` 时必填。

    Returns:
        :class:`BacktestReport`。
    """
    sell_config = SellRuleConfig(
        take_profit=None, stop_loss=stop_loss, hold_sellable_days=hold_sellable_days
    )
    ordered_samples = sorted(samples, key=lambda sample: (sample.D, sample.code))
    segments = split_segments(ordered_samples, cuts=cuts)
    filters = _segment_filters(segments)

    if factor_params is None:
        factor_ids: list[str] = []
        for path in paths:
            factor_ids.extend(spec.factor_id for spec in path.gates)
            factor_ids.extend(spec.factor_id for spec in path.bonus)
        # 只解析**已注册因子**；策略自身命名空间（如 ``dragon``）由下面的 ``params`` 注入。
        registered = [factor_id for factor_id in dict.fromkeys(factor_ids) if _is_factor(factor_id)]
        resolved = await resolve_factor_params(registered, repos)
        resolved.setdefault(strategy_id, dict(params))
    else:
        resolved = {key: dict(value) for key, value in factor_params.items()}

    ordered_paths = sorted(paths, key=lambda path: path.priority)
    segment_reports: list[SegmentReport] = []
    for name in SEGMENT_ORDER:
        bucket = segments.get(name)
        segment_reports.append(
            SegmentReport(
                name=name,
                sample_count=len(bucket),
                paths=tuple(
                    _path_segment_stats(path, bucket, resolved, sell_config)
                    for path in ordered_paths
                ),
                portfolio=sim(
                    ordered_paths,
                    bucket,
                    factor_params=resolved,
                    cap=cap,
                    bonus_step=bonus_step,
                    sell_config=sell_config,
                    include=filters[name],
                ).stats,
            )
        )

    recommended = _portfolio_by_segment(
        ordered_paths,
        ordered_samples,
        filters,
        resolved,
        cap=cap,
        bonus_step=bonus_step,
        sell_config=sell_config,
    )
    basic = _portfolio_by_segment(
        [path.without_bonus_scaling() for path in ordered_paths],
        ordered_samples,
        filters,
        resolved,
        cap=cap,
        bonus_step=bonus_step,
        sell_config=sell_config,
    )
    variant_results: dict[str, dict[str, PortfolioStats]] = {}
    for name, variant_paths in (variants or {}).items():
        variant_results[name] = _portfolio_by_segment(
            sorted(variant_paths, key=lambda path: path.priority),
            ordered_samples,
            filters,
            resolved,
            cap=cap,
            bonus_step=bonus_step,
            sell_config=sell_config,
        )

    full = next((item for item in segment_reports if item.name == SEGMENT_ALL), None)
    report = BacktestReport(
        strategy_id=strategy_id,
        start=ordered_samples[0].D if ordered_samples else date.min,
        end=ordered_samples[-1].D if ordered_samples else date.min,
        params=dict(params),
        path_ids=tuple(path.path_id for path in ordered_paths),
        segments=tuple(segment_reports),
        caliber={"推荐版": recommended, "基础版": basic},
        variants=variant_results,
        trigger_count=full.portfolio.trigger_count if full is not None else 0,
    )
    if repos is not None:
        await _persist(repos, report, run_id, strategy_id)
    return report


async def _persist(
    repos: Any, report: BacktestReport, run_id: str | None, strategy_id: str
) -> None:
    """把报告幂等落 ``backtest_runs``（同 ``run_id`` 重跑覆盖结果，不新增行）。"""
    if not run_id:
        raise ValueError("落库需要显式 run_id")
    existing = await repos.backtest_runs.get(run_id)
    if existing is None:
        await repos.backtest_runs.create(
            run_id,
            report.start,
            report.end,
            [strategy_id],
            dict(report.params),
        )
        await repos.backtest_runs.update_status(run_id, "running")
    await repos.backtest_runs.update_status(run_id, "succeeded", report=report.to_dict())
