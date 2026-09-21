"""策略插件协议层：阶段、情绪周期态、门控声明、参数解析与策略基类。

设计目标（spec「策略插件框架」）：

- **门控下沉**：情绪周期门控由策略自身声明 :attr:`BaseStrategy.gate_matrix`，
  核心 SHALL NOT 维护「以策略名为 key」的硬编码矩阵（旧项目 ``cycle.py::_GATE``
  的缺陷：新增策略若未改核心文件，门控会静默取到 ``(False, 0.0)`` 而被禁用）。
- **能力声明化**：旧项目的 ``has_auction_advice`` / ``has_pool_phase`` /
  ``has_intraday_phase`` 布尔标记，改为声明式 :attr:`BaseStrategy.phases`
  （:class:`Phase` 的 ``frozenset``），阶段与生命周期钩子一一对应。
- **参数隔离**：运行时覆盖存放于 :class:`contextvars.ContextVar`（以 ``strategy_id``
  为命名空间），并发任务互不可见；解析顺序为
  **运行时覆盖 > DB active 配置 > 代码默认**（与因子层语义一致）。

情绪周期态映射（:class:`CycleState` ← 参考实现
``quant-system/src/quant_system/cycle.py`` 的 ``CycleState``）::

    冰点(ICE) / 冰点转折(TURN) / 修复(REPAIR) / 加速·高潮(ACCEL) / 分歧(DIVERGE) / 退潮(RETREAT)

任务书中「冰点/修复/启动/加速/高潮/退潮」为口语化近似：其中「启动」对应 ``TURN``
（冰点转折），「加速/高潮」为同一态 ``ACCEL``。另含 :attr:`CycleState.UNKNOWN`，
用于关键指标缺失、判定降级时的兜底（门控评估见
:func:`app.strategies.registry.evaluate_gate`）。
"""

from __future__ import annotations

import logging
from abc import ABC
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, TypeVar, cast, runtime_checkable

from app.factors.base import FactorParamSpec

if TYPE_CHECKING:
    from app.strategies.context import StrategyContext

__all__ = [
    "DEFAULT_GATE_POSITION_FACTOR",
    "PHASE_HOOKS",
    "AdviceReportRepo",
    "BaseStrategy",
    "CycleState",
    "GateDecision",
    "GateRule",
    "Phase",
    "ResolvedStrategyParams",
    "StrategyConfigRepo",
    "StrategyDefRepo",
    "StrategyParamSpec",
    "StrategyRegistryError",
    "StrategyRepos",
    "current_overrides",
    "override_params",
    "resolve_params",
]

logger = logging.getLogger(__name__)

#: 策略参数规格直接复用因子参数规格（同形 → 前端可复用同一表单渲染器）。
StrategyParamSpec = FactorParamSpec

#: 周期态未在策略 ``gate_matrix`` 中声明时的默认仓位系数（放行但不重仓）。
DEFAULT_GATE_POSITION_FACTOR = 0.5


class Phase(StrEnum):
    """策略参与的生命周期阶段（与调度器时间轴对齐）。"""

    AUCTION = "auction"      # 9:25 竞价阶段
    OPENING = "opening"      # 9:25 撮合价就绪后的开盘判定（次日开盘买点提示）
    POOL = "pool"            # 盘后建池
    SCENE = "scene"          # 开盘场景分类
    INTRADAY = "intraday"    # 盘中确认
    TAILPAN = "tailpan"      # 尾盘处理


class CycleState(StrEnum):
    """情绪周期态（六态 + 降级态；取值来自参考实现 ``cycle.py``）。"""

    ICE = "冰点"
    TURN = "冰点转折"
    REPAIR = "修复"
    ACCEL = "加速/高潮"
    DIVERGE = "分歧"
    RETREAT = "退潮"
    UNKNOWN = "未知"


#: 阶段 → 生命周期钩子方法名（``BaseStrategy.execute`` 据此分派）。
PHASE_HOOKS: dict[Phase, str] = {
    Phase.AUCTION: "run_auction_pipeline",
    Phase.OPENING: "confirm_opening",
    Phase.POOL: "build_pool",
    Phase.SCENE: "classify_scenes",
    Phase.INTRADAY: "confirm_intraday",
    Phase.TAILPAN: "run_tailpan",
}


class StrategyRegistryError(RuntimeError):
    """策略注册表异常（重复 ``strategy_id`` / 未注册 / 缺少标识）。"""


# ============================================================ 门控声明


@dataclass(frozen=True, slots=True)
class GateRule:
    """策略对某个周期态的门控声明（``gate_matrix`` 的一项）。

    Attributes:
        allowed: 该周期态下是否允许开仓。
        position_factor: 仓位系数（1.0 = 满额，0.0 = 禁用）。
    """

    allowed: bool
    position_factor: float


@dataclass(frozen=True, slots=True)
class GateDecision:
    """门控评估结果（:func:`app.strategies.registry.evaluate_gate` 产出）。

    Attributes:
        allowed: 是否允许开仓。
        position_factor: 生效仓位系数。
        reason: 人类可读的判定说明（含来源与默认回退提示）。
        state: 被评估的周期态。
    """

    allowed: bool
    position_factor: float
    reason: str
    state: CycleState


# ============================================================ 参数覆盖（contextvars）


#: 运行时覆盖：``{strategy_id: {param_key: value}}``；默认 ``None`` 表示无覆盖。
#: 刻意使用 :class:`ContextVar` 而非模块级全局 dict——后者在并发运行
#: （多 asyncio 任务 / 多线程）下会互相串台（旧项目 ``config_registry._OVERRIDES`` 缺陷）。
_OVERRIDES: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "strategy_param_overrides", default=None
)


@contextmanager
def override_params(strategy_id: str, params: Mapping[str, Any]) -> Iterator[None]:
    """在 ``with`` 块内覆盖某策略的参数（contextvars，任务间隔离）。

    嵌套使用时按 ``strategy_id`` 合并，退出时逐层还原，不影响其他并发任务。

    Args:
        strategy_id: 目标策略标识。
        params: ``{参数键: 值}``，仅覆盖列出的键。
    """
    current = dict(_OVERRIDES.get() or {})
    current[strategy_id] = dict(params)
    token = _OVERRIDES.set(current)
    try:
        yield
    finally:
        _OVERRIDES.reset(token)


def current_overrides() -> dict[str, dict[str, Any]]:
    """返回当前上下文中的覆盖快照（拷贝，避免外部改动影响运行）。"""
    return {key: dict(value) for key, value in (_OVERRIDES.get() or {}).items()}


# ============================================================ 参数解析


@runtime_checkable
class StrategyConfigRepo(Protocol):
    """``StrategyConfigRepository`` 的结构化子集（避免策略层 import 仓储包）。"""

    async def get_active(self, owner_id: str) -> Any | None: ...


@runtime_checkable
class StrategyDefRepo(Protocol):
    """``StrategyDefRepository`` 的结构化子集。"""

    async def get(self, strategy_id: str) -> Any | None: ...

    async def list_all(self) -> list[Any]: ...

    async def upsert(
        self,
        strategy_id: str,
        label: str,
        version: str,
        params_schema: dict[str, Any],
        gate_matrix: dict[str, Any],
        *,
        description: str | None = None,
        enabled: bool = True,
    ) -> Any: ...


@runtime_checkable
class AdviceReportRepo(Protocol):
    """``AdviceReportRepository`` 的结构化子集（仅落失败记录所需）。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int: ...


@runtime_checkable
class StrategyRepos(Protocol):
    """策略解析/同步/执行所需的最小仓储视图（``Repositories`` 结构兼容）。"""

    session: Any
    strategy_defs: StrategyDefRepo
    strategy_configs: StrategyConfigRepo
    advice_reports: AdviceReportRepo


@dataclass(frozen=True, slots=True)
class ResolvedStrategyParams:
    """策略参数解析结果。

    Attributes:
        strategy_id: 策略标识。
        params: 生效参数。
        version: 参数版本串（``"default"`` / ``"v3"`` / ``"v3+override"``）。
        source: 生效来源（``default`` / ``active`` / ``override``）。
        warnings: 非法值回退告警列表。
    """

    strategy_id: str
    params: dict[str, Any]
    version: str
    source: str
    warnings: tuple[dict[str, Any], ...] = ()


def _coerce(spec: FactorParamSpec, value: Any) -> tuple[Any, str | None]:
    """校验并归一化单个参数值。

    Returns:
        ``(归一化值, 失败原因)``；失败原因非 ``None`` 时归一化值为代码默认值。
    """
    if spec.type == "bool":
        if isinstance(value, bool):
            return value, None
        return spec.default, f"期望 bool，实际 {type(value).__name__}"
    if spec.type == "enum":
        if isinstance(value, str):
            return value, None
        return spec.default, f"期望 str，实际 {type(value).__name__}"
    if spec.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return spec.default, f"期望 int，实际 {type(value).__name__}"
        number: float = value
    else:  # float / percent
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return spec.default, f"期望数值，实际 {type(value).__name__}"
        number = float(value)
    if spec.min is not None and number < spec.min:
        return spec.default, f"低于下限 {spec.min}"
    if spec.max is not None and number > spec.max:
        return spec.default, f"高于上限 {spec.max}"
    return (int(number) if spec.type == "int" else number), None


def _apply(
    strategy_id: str,
    schema: Sequence[FactorParamSpec],
    current: dict[str, Any],
    values: Mapping[str, Any],
    *,
    source: str,
) -> list[dict[str, Any]]:
    """把 ``values`` 校验后并入 ``current``，返回结构化告警列表。"""
    warnings: list[dict[str, Any]] = []
    for spec in schema:
        if spec.key not in values:
            continue
        raw = values[spec.key]
        coerced, reason = _coerce(spec, raw)
        if reason is not None:
            warning = {
                "strategy_id": strategy_id,
                "key": spec.key,
                "value": raw,
                "source": source,
                "reason": reason,
                "fallback": spec.default,
            }
            warnings.append(warning)
            logger.warning("strategy_param_invalid", extra=warning)
        current[spec.key] = coerced
    return warnings


async def resolve_params(
    strategy: type[BaseStrategy] | BaseStrategy,
    repos: StrategyRepos | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> ResolvedStrategyParams:
    """按「运行时覆盖 > active 配置 > 代码默认」解析策略参数。

    每个策略的配置以其 ``strategy_id`` 为命名空间，故两个策略即使都定义同名
    参数（如 ``threshold``）也互不影响。

    Args:
        strategy: 策略类或实例。
        repos: 仓储容器；为 ``None`` 时跳过 DB（仅用覆盖与代码默认）。
        overrides: 显式覆盖；``None`` 时读取当前上下文（:func:`override_params`）。
    """
    cls: type[BaseStrategy] = strategy if isinstance(strategy, type) else type(strategy)
    instance = cls()
    current = instance.default_params()
    version = "default"
    source = "default"
    warnings: list[dict[str, Any]] = []

    if repos is not None:
        active = await repos.strategy_configs.get_active(cls.strategy_id)
        if active is not None:
            warnings.extend(
                _apply(
                    cls.strategy_id,
                    cls.params_schema,
                    current,
                    dict(active.params),
                    source="active",
                )
            )
            version = f"v{int(active.version)}"
            source = "active"

    effective = overrides if overrides is not None else current_overrides().get(cls.strategy_id)
    if effective:
        warnings.extend(
            _apply(cls.strategy_id, cls.params_schema, current, dict(effective), source="override")
        )
        version = f"{version}+override"
        source = "override"

    return ResolvedStrategyParams(
        strategy_id=cls.strategy_id,
        params=current,
        version=version,
        source=source,
        warnings=tuple(warnings),
    )


# ============================================================ 策略基类


class BaseStrategy(ABC):  # noqa: B024 - 生命周期钩子均为可选（默认 no-op），故无抽象方法
    """策略基类：声明式元数据 + 可选生命周期钩子。

    子类须声明类属性 ``strategy_id`` / ``label`` / ``version`` / ``description`` /
    ``phases`` / ``params_schema`` / ``gate_matrix``；生命周期钩子均为可选实现
    （默认 no-op），未声明的阶段不会被调度。
    """

    strategy_id: ClassVar[str] = ""
    label: ClassVar[str] = ""
    version: ClassVar[str] = ""
    description: ClassVar[str] = ""
    #: 本策略参与的阶段；调度器据此筛选，无需核心维护策略名清单。
    phases: ClassVar[frozenset[Phase]] = frozenset()
    #: 参数 schema（与因子同形，供前端自动渲染表单）。
    params_schema: ClassVar[tuple[StrategyParamSpec, ...]] = ()
    #: 门控矩阵：周期态 → :class:`GateRule`，**由策略自身声明**（核心不再硬编码）。
    gate_matrix: ClassVar[Mapping[CycleState, GateRule]] = {}

    # ------------------------------------------------------------ 生命周期钩子

    async def run_auction_pipeline(self, ctx: StrategyContext) -> Any:
        """竞价阶段钩子（默认 no-op）。"""
        return None

    async def build_pool(self, ctx: StrategyContext) -> Any:
        """盘后建池钩子（默认 no-op）。"""
        return None

    async def classify_scenes(self, ctx: StrategyContext) -> Any:
        """开盘场景分类钩子（默认 no-op）。"""
        return None

    async def confirm_opening(self, ctx: StrategyContext) -> Any:
        """开盘判定钩子（09:25 撮合价就绪后；默认 no-op）。"""
        return None

    async def confirm_intraday(self, ctx: StrategyContext) -> Any:
        """盘中确认钩子（默认 no-op）。"""
        return None

    async def run_tailpan(self, ctx: StrategyContext) -> Any:
        """尾盘处理钩子（默认 no-op）。"""
        return None

    async def intraday_expired(self, ctx: StrategyContext) -> bool:
        """分时是否已越过观察窗口且未触发（默认 ``False``）。"""
        return False

    async def execute(self, phase: Phase, ctx: StrategyContext) -> Any:
        """按 ``phase`` 分派到对应生命周期钩子（未实现者返回 ``None``）。"""
        hook = PHASE_HOOKS[phase]
        return await getattr(self, hook)(ctx)

    # ------------------------------------------------------------ 参数

    def default_params(self) -> dict[str, Any]:
        """返回全部参数的代码默认值。"""
        return {spec.key: spec.default for spec in self.params_schema}

    def param(self, params: Mapping[str, Any], key: str) -> Any:
        """取参数值，缺失时回退到该参数的 schema 默认值。

        Raises:
            KeyError: 策略未声明该参数（防止拼写错误静默取到错误阈值）。
        """
        for spec in self.params_schema:
            if spec.key == key:
                value = params.get(key, spec.default)
                return spec.default if value is None else value
        raise KeyError(f"{self.strategy_id} 未声明参数 {key!r}")

    async def get_params(self, ctx: StrategyContext) -> dict[str, Any]:
        """返回本策略的生效参数（覆盖 > active 配置 > 代码默认）。

        ``ctx.params`` 已由 :class:`~app.strategies.context.StrategyContextFactory`
        预解析时直接复用；否则现场按同一顺序解析（便于手工构造的轻量上下文）。
        """
        if ctx.params:
            return dict(ctx.params)
        resolved = await resolve_params(type(self), cast("StrategyRepos", ctx.repos))
        return resolved.params

    # ------------------------------------------------------------ schema

    def params_schema_dict(self) -> dict[str, Any]:
        """参数 schema 的 JSON 形态（写入 ``strategy_defs.params_schema``）。"""
        return {"params": [spec.to_dict() for spec in self.params_schema]}

    def gate_matrix_dict(self) -> dict[str, Any]:
        """门控矩阵的 JSON 形态（写入 ``strategy_defs.gate_matrix``）。"""
        return {
            state.value: {"allowed": rule.allowed, "position_factor": rule.position_factor}
            for state, rule in self.gate_matrix.items()
        }

    def schema_dict(self) -> dict[str, Any]:
        """完整 schema（供前端渲染表单与 Agent 查询）。"""
        return {
            "strategy_id": self.strategy_id,
            "label": self.label,
            "version": self.version,
            "description": self.description,
            "phases": sorted(phase.value for phase in self.phases),
            "gate_matrix": self.gate_matrix_dict(),
            **self.params_schema_dict(),
        }


StrategyT = TypeVar("StrategyT", bound=BaseStrategy)
