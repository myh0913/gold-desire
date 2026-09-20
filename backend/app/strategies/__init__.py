"""策略插件框架：目录式发现、隔离执行、策略自声明门控与参数命名空间。

分层：

- :mod:`app.strategies.protocol` — 策略协议、阶段/周期态、门控声明与参数解析；
- :mod:`app.strategies.context` — 依赖注入上下文与只读门面；
- :mod:`app.strategies.registry` — 注册/发现/门控评估/隔离执行/定义同步；
- :mod:`app.strategies.loader` — 插件目录扫描与容错导入；
- :mod:`app.strategies.plugins` — **生产插件目录**（新增策略 = 在此新增目录）；
- :mod:`app.strategies.examples` — 示例实现（不参与生产发现）。

导入本包即对默认 ``plugins/`` 目录执行一次非严格发现（空目录无副作用）；应用启动时
应显式调用 :func:`discover_plugins`（``strict=True``）以在重复 ``strategy_id`` 时快速失败，
并调用 :func:`sync_definitions` 让 DB 反映代码。
"""

from app.strategies.context import (
    DataFacade,
    DefaultFactorFacade,
    FactorFacade,
    StrategyContext,
    StrategyContextFactory,
)
from app.strategies.loader import (
    DEFAULT_PLUGINS_DIR,
    PLUGINS_PACKAGE,
    DiscoveryResult,
    PluginImportError,
)
from app.strategies.protocol import (
    DEFAULT_GATE_POSITION_FACTOR,
    PHASE_HOOKS,
    BaseStrategy,
    CycleState,
    GateDecision,
    GateRule,
    Phase,
    ResolvedStrategyParams,
    StrategyParamSpec,
    StrategyRegistryError,
    StrategyRepos,
    current_overrides,
    override_params,
    resolve_params,
)
from app.strategies.registry import (
    ADVICE_KIND_ERROR,
    PhaseRunSummary,
    StrategyRunResult,
    all_strategies,
    clear_registry,
    discover_plugins,
    evaluate_gate,
    export_schemas,
    get_strategy,
    register_strategy,
    run_phase,
    set_enabled,
    strategies_with_phase,
    sync_definitions,
)

__all__ = [
    "ADVICE_KIND_ERROR",
    "DEFAULT_GATE_POSITION_FACTOR",
    "DEFAULT_PLUGINS_DIR",
    "PHASE_HOOKS",
    "PLUGINS_PACKAGE",
    "BaseStrategy",
    "CycleState",
    "DataFacade",
    "DefaultFactorFacade",
    "DiscoveryResult",
    "FactorFacade",
    "GateDecision",
    "GateRule",
    "Phase",
    "PhaseRunSummary",
    "PluginImportError",
    "ResolvedStrategyParams",
    "StrategyContext",
    "StrategyContextFactory",
    "StrategyParamSpec",
    "StrategyRegistryError",
    "StrategyRepos",
    "StrategyRunResult",
    "all_strategies",
    "clear_registry",
    "current_overrides",
    "discover_plugins",
    "evaluate_gate",
    "export_schemas",
    "get_strategy",
    "override_params",
    "register_strategy",
    "resolve_params",
    "run_phase",
    "set_enabled",
    "strategies_with_phase",
    "sync_definitions",
]

# 导入本包即对默认 ``plugins/`` 目录执行一次**非严格**发现（空目录无副作用）：
# 坏插件记告警后继续，重复 ``strategy_id`` 则直接抛出。启动路径应再以
# ``discover_plugins(strict=True)`` 调用一次并在随后 ``sync_definitions(repos)``，
# 使 DB 反映代码。
discover_plugins()
