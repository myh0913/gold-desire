"""策略注册表：自动发现、查询、门控评估、隔离执行与定义同步。

关键职责（spec「策略插件框架」）：

- **目录式自动发现**：扫描 ``app/strategies/plugins/*/`` 下的插件包并注册；
  重复 ``strategy_id`` 在发现/启动时**直接报错**（错误信息同时点出两个模块）。
- **隔离执行**：:func:`run_phase` 逐个执行已启用策略，**单个策略异常不中断整批**：
  捕获、记录结构化错误、落一条 ``kind="error"`` 的建议报告（供 UI/Agent 查询）后继续。
- **门控来自策略声明**：:func:`evaluate_gate` 只读策略自身的 ``gate_matrix``，
  核心不维护任何以策略名为 key 的硬编码矩阵。
- **参数命名空间**：每个策略的配置以其 ``strategy_id`` 为命名空间（见
  :mod:`app.strategies.protocol`），同名参数互不串台。
- **启停**：以 ``strategy_defs.enabled`` 为准；未同步的策略默认视为启用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from app.strategies.protocol import (
    DEFAULT_GATE_POSITION_FACTOR,
    BaseStrategy,
    CycleState,
    GateDecision,
    Phase,
    StrategyRegistryError,
    StrategyRepos,
)

if TYPE_CHECKING:
    from app.strategies.context import StrategyContextFactory
    from app.strategies.loader import DiscoveryResult

__all__ = [
    "ADVICE_KIND_ERROR",
    "PhaseRunSummary",
    "StrategyRunResult",
    "all_strategies",
    "clear_registry",
    "discover_plugins",
    "evaluate_gate",
    "export_schemas",
    "get_strategy",
    "register_strategy",
    "run_phase",
    "set_enabled",
    "strategies_with_phase",
    "sync_definitions",
]

logger = logging.getLogger(__name__)

#: 策略执行失败写入 ``advice_reports`` 时的报告类型。
ADVICE_KIND_ERROR = "error"

_REGISTRY: dict[str, type[BaseStrategy]] = {}

StrategyT = TypeVar("StrategyT", bound=BaseStrategy)


# ============================================================ 注册


def register_strategy(cls: type[StrategyT]) -> type[StrategyT]:
    """注册策略类（装饰器用法），导入即生效；同一类重复注册幂等。

    Raises:
        StrategyRegistryError: 缺少 ``strategy_id``，或 ``strategy_id`` 已被
            **另一个类**占用（错误信息同时点出两个模块，便于定位冲突插件）。
    """
    if not cls.strategy_id:
        raise StrategyRegistryError(f"{cls.__qualname__} 缺少 strategy_id")
    existing = _REGISTRY.get(cls.strategy_id)
    if existing is cls:
        return cls
    if existing is not None:
        raise StrategyRegistryError(
            f"重复的 strategy_id {cls.strategy_id!r}："
            f"{existing.__module__}.{existing.__qualname__} 与 "
            f"{cls.__module__}.{cls.__qualname__}"
        )
    _REGISTRY[cls.strategy_id] = cls
    return cls


def clear_registry() -> None:
    """清空注册表（仅供测试隔离使用）。"""
    _REGISTRY.clear()


def all_strategies() -> list[type[BaseStrategy]]:
    """返回全部已注册策略类（按 ``strategy_id`` 升序）。"""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def get_strategy(strategy_id: str) -> type[BaseStrategy]:
    """按标识取策略类。

    Raises:
        StrategyRegistryError: 未注册。
    """
    try:
        return _REGISTRY[strategy_id]
    except KeyError as exc:
        raise StrategyRegistryError(
            f"未注册的策略: {strategy_id!r}（已注册：{sorted(_REGISTRY)}）"
        ) from exc


# ============================================================ 发现


def discover_plugins(
    *,
    directory: Path | str | None = None,
    package: str | None = None,
    strict: bool = False,
) -> DiscoveryResult:
    """扫描插件目录、导入并注册策略（委托 :mod:`app.strategies.loader`）。

    Args:
        directory: 插件目录；``None`` 用默认的 ``app/strategies/plugins``。
        package: 生成模块名所用的包前缀；``None`` 用默认前缀。
        strict: 遇导入错误时是否直接抛出（启动/测试用）；``False`` 时收集错误继续。
    """
    from app.strategies.loader import discover_plugins as _discover

    return _discover(directory=directory, package=package, strict=strict)


# ============================================================ 查询


async def _disabled_ids(repos: StrategyRepos | None) -> set[str]:
    """取已停用策略标识集合；未同步到 DB 的策略默认启用。"""
    if repos is None:
        return set()
    rows = await repos.strategy_defs.list_all()
    return {str(row.strategy_id) for row in rows if not bool(row.enabled)}


async def strategies_with_phase(
    phase: Phase, repos: StrategyRepos | None = None
) -> list[type[BaseStrategy]]:
    """返回参与该阶段且**未被停用**的策略类（按 ``strategy_id`` 升序）。"""
    disabled = await _disabled_ids(repos)
    return [
        cls for cls in all_strategies() if phase in cls.phases and cls.strategy_id not in disabled
    ]


def export_schemas() -> list[dict[str, Any]]:
    """导出全部策略的完整 schema（供前端自动渲染与 Agent 查询）。"""
    return [cls().schema_dict() for cls in all_strategies()]


# ============================================================ 门控


def evaluate_gate(strategy: type[BaseStrategy] | BaseStrategy, state: CycleState) -> GateDecision:
    """按策略自身声明的 ``gate_matrix`` 评估周期门控。

    回退口径（**绝不静默禁用**）：当 ``state`` 为 :attr:`CycleState.UNKNOWN`
    或未在矩阵中声明时，记一条 ``strategy_gate_fallback`` 告警，并按文档化默认
    「放行 + 仓位系数 :data:`~app.strategies.protocol.DEFAULT_GATE_POSITION_FACTOR`」处理。
    """
    cls: type[BaseStrategy] = strategy if isinstance(strategy, type) else type(strategy)
    rule = (cls.gate_matrix or {}).get(state)
    if rule is None:
        reason = (
            f"{cls.strategy_id} 未声明周期态 {state.value!r} 的门控；按默认放行"
            f"（allowed=True, position_factor={DEFAULT_GATE_POSITION_FACTOR}），"
            "请为该策略补充 gate_matrix"
        )
        logger.warning(
            "strategy_gate_fallback",
            extra={
                "strategy_id": cls.strategy_id,
                "state": state.value,
                "default_position_factor": DEFAULT_GATE_POSITION_FACTOR,
            },
        )
        return GateDecision(
            allowed=True,
            position_factor=DEFAULT_GATE_POSITION_FACTOR,
            reason=reason,
            state=state,
        )
    reason = f"{state.value}：{'允许' if rule.allowed else '禁用'}，仓位系数 {rule.position_factor}"
    return GateDecision(
        allowed=rule.allowed,
        position_factor=rule.position_factor,
        reason=reason,
        state=state,
    )


# ============================================================ 定义同步 / 启停


async def sync_definitions(repos: StrategyRepos) -> list[str]:
    """把全部已注册策略的定义幂等 upsert 进 ``strategy_defs``（DB 始终反映代码）。

    已存在定义的 ``enabled`` 状态会被保留（不因重新同步而意外启用）。

    Returns:
        已同步的 ``strategy_id`` 列表（升序）。
    """
    # 先过期身份映射：``bulk_upsert`` 直写 SQL，不会刷新 ORM 缓存，否则随后读取到的
    # ``enabled`` 可能是陈旧值（会把刚停用的策略重新启用）。
    repos.session.expire_all()

    synced: list[str] = []
    for cls in all_strategies():
        instance = cls()
        existing = await repos.strategy_defs.get(cls.strategy_id)
        enabled = True if existing is None else bool(existing.enabled)
        await repos.strategy_defs.upsert(
            cls.strategy_id,
            cls.label,
            cls.version,
            instance.params_schema_dict(),
            instance.gate_matrix_dict(),
            description=cls.description,
            enabled=enabled,
        )
        synced.append(cls.strategy_id)
    return synced


async def set_enabled(strategy_id: str, enabled: bool, repos: StrategyRepos) -> bool:
    """启用/停用策略（幂等写入 ``strategy_defs``），返回是否成功。

    Raises:
        StrategyRegistryError: 策略未注册。
    """
    cls = get_strategy(strategy_id)
    instance = cls()
    await repos.strategy_defs.upsert(
        cls.strategy_id,
        cls.label,
        cls.version,
        instance.params_schema_dict(),
        instance.gate_matrix_dict(),
        description=cls.description,
        enabled=enabled,
    )
    # ``upsert`` 走 bulk SQL，不刷新 ORM 缓存；过期后随后的读取才能看到新状态。
    repos.session.expire_all()
    return True


# ============================================================ 隔离执行


@dataclass(frozen=True, slots=True)
class StrategyRunResult:
    """单个策略在一次阶段运行中的结果。

    Attributes:
        strategy_id: 策略标识。
        ok: 是否成功。
        output: 钩子返回值（成功时）。
        error: 错误摘要（失败时，``"类型: 信息"``）。
    """

    strategy_id: str
    ok: bool
    output: Any = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PhaseRunSummary:
    """一次阶段运行的汇总（逐策略结果 + 成功/失败计数）。"""

    phase: Phase
    trade_date: date
    results: tuple[StrategyRunResult, ...] = ()

    @property
    def success_count(self) -> int:
        """成功策略数。"""
        return sum(1 for item in self.results if item.ok)

    @property
    def failure_count(self) -> int:
        """失败策略数。"""
        return sum(1 for item in self.results if not item.ok)

    @property
    def succeeded_ids(self) -> list[str]:
        """成功策略标识列表。"""
        return [item.strategy_id for item in self.results if item.ok]

    @property
    def failed_ids(self) -> list[str]:
        """失败策略标识列表。"""
        return [item.strategy_id for item in self.results if not item.ok]


async def _record_failure(
    repos: StrategyRepos,
    trade_date: date,
    strategy_id: str,
    phase: Phase,
    exc: BaseException,
) -> None:
    """把策略失败落为一条 ``kind="error"`` 的建议报告（供 UI/Agent 查询）。

    落库本身失败不影响阶段继续（仅记日志）。
    """
    entry: dict[str, Any] = {
        "trade_date": trade_date,
        "kind": ADVICE_KIND_ERROR,
        "strategy_id": strategy_id,
        "strategy_version": None,
        "payload": {
            "phase": phase.value,
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
        "ran_at": datetime.now(UTC),
    }
    try:
        await repos.advice_reports.upsert_many([entry])
    except Exception:  # pragma: no cover - 落库失败不应影响阶段继续
        logger.exception(
            "strategy_failure_record_failed",
            extra={"strategy_id": strategy_id, "phase": phase.value},
        )


async def run_phase(
    phase: Phase,
    ctx_factory: StrategyContextFactory,
    repos: StrategyRepos,
) -> PhaseRunSummary:
    """执行某阶段下全部已启用策略，**逐策略隔离**，单个失败不中断整批。

    Args:
        phase: 目标阶段。
        ctx_factory: 策略上下文工厂（提供 ``trade_date`` 与依赖装配）。
        repos: 仓储容器（用于筛选启用策略与落失败记录）。

    Returns:
        逐策略结果与成功/失败计数的 :class:`PhaseRunSummary`。
    """
    results: list[StrategyRunResult] = []
    for cls in await strategies_with_phase(phase, repos):
        try:
            ctx = await ctx_factory.create(cls.strategy_id)
            output = await cls().execute(phase, ctx)
        except Exception as exc:
            logger.exception(
                "strategy_phase_failed",
                extra={
                    "strategy_id": cls.strategy_id,
                    "phase": phase.value,
                    "trade_date": str(ctx_factory.trade_date),
                },
            )
            await _record_failure(repos, ctx_factory.trade_date, cls.strategy_id, phase, exc)
            results.append(
                StrategyRunResult(
                    strategy_id=cls.strategy_id,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            results.append(StrategyRunResult(strategy_id=cls.strategy_id, ok=True, output=output))
    return PhaseRunSummary(phase=phase, trade_date=ctx_factory.trade_date, results=tuple(results))
