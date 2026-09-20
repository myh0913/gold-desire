"""策略插件框架测试（SQLite 内存库，无需 PostgreSQL / Redis / 网络）。

覆盖：

1. 目录式自动发现（含重复 ``strategy_id`` 报错与坏插件容错）；
2. 门控来自策略**自身声明**的 ``gate_matrix``（核心零改动）；
3. 策略隔离执行（一个抛异常不影响其他，失败被记录）；
4. 参数命名空间（同名 ``threshold`` 互不串台）；
5. 参数解析顺序 + 并发覆盖隔离（contextvars，回归旧全局 dict 缺陷）；
6. 启用/停用；
7. 静态架构测试（插件禁止 import 仓储/数据源/provider）；
8. ``sync_definitions`` 幂等。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import ClassVar

import pytest
from app.core.config import get_settings
from app.db.base import Base
from app.repositories import Repositories
from app.strategies import (
    DEFAULT_GATE_POSITION_FACTOR,
    BaseStrategy,
    CycleState,
    GateRule,
    Phase,
    StrategyContextFactory,
    StrategyParamSpec,
    StrategyRegistryError,
    all_strategies,
    clear_registry,
    current_overrides,
    discover_plugins,
    evaluate_gate,
    export_schemas,
    get_strategy,
    override_params,
    register_strategy,
    resolve_params,
    run_phase,
    set_enabled,
    strategies_with_phase,
    sync_definitions,
)
from app.strategies import protocol as protocol_module
from app.strategies import registry as registry_module
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

TRADE_DATE = date(2026, 9, 18)

#: 复用的阈值参数声明（多个测试策略共享同一 ``threshold`` 键，用于验证命名空间）。
THRESHOLD_SPEC = StrategyParamSpec(
    key="threshold", label="阈值", type="float", default=0.5, min=0.0, max=1.0
)


# ============================================================ 测试用策略


class AlphaStrategy(BaseStrategy):
    """隔离测试：正常策略。"""

    strategy_id = "test_alpha"
    label = "测试甲"
    version = "1.0.0"
    description = "隔离测试"
    phases = frozenset({Phase.POOL})
    params_schema = (THRESHOLD_SPEC,)
    gate_matrix: ClassVar[Mapping[CycleState, GateRule]] = {
        CycleState.REPAIR: GateRule(True, 0.5),
        CycleState.RETREAT: GateRule(False, 0.0),
    }

    async def build_pool(self, ctx: object) -> dict[str, str]:
        return {"strategy": self.strategy_id}


class BoomStrategy(BaseStrategy):
    """隔离测试：执行时抛异常（排序后位于中间）。"""

    strategy_id = "test_boom"
    label = "测试乙"
    version = "1.0.0"
    description = "隔离测试（异常）"
    phases = frozenset({Phase.POOL})
    gate_matrix: ClassVar[Mapping[CycleState, GateRule]] = {
        CycleState.REPAIR: GateRule(True, 0.5)
    }

    async def build_pool(self, ctx: object) -> None:
        raise RuntimeError("boom")


class GammaStrategy(BaseStrategy):
    """隔离测试：正常策略。"""

    strategy_id = "test_gamma"
    label = "测试丙"
    version = "1.0.0"
    description = "隔离测试"
    phases = frozenset({Phase.POOL})
    gate_matrix: ClassVar[Mapping[CycleState, GateRule]] = {
        CycleState.REPAIR: GateRule(True, 0.5)
    }

    async def build_pool(self, ctx: object) -> str:
        return self.strategy_id


class GateDemoStrategy(BaseStrategy):
    """门控测试：仅声明 REPAIR / RETREAT 两个周期态。"""

    strategy_id = "test_gate_demo"
    label = "测试门控"
    version = "1.0.0"
    description = "门控测试"
    phases = frozenset({Phase.POOL})
    gate_matrix: ClassVar[Mapping[CycleState, GateRule]] = {
        CycleState.REPAIR: GateRule(True, 0.42),
        CycleState.RETREAT: GateRule(False, 0.0),
    }


class IntradayOnlyStrategy(BaseStrategy):
    """阶段筛选测试：只参与盘中确认。"""

    strategy_id = "test_intraday_only"
    label = "测试盘中"
    version = "1.0.0"
    description = "阶段筛选测试"
    phases = frozenset({Phase.INTRADAY})


class NsAlphaStrategy(BaseStrategy):
    """参数命名空间测试：与 NsBeta 同名参数 ``threshold``。"""

    strategy_id = "test_ns_alpha"
    label = "命名空间甲"
    version = "1.0.0"
    description = "命名空间测试"
    phases = frozenset({Phase.POOL})
    params_schema = (THRESHOLD_SPEC,)


class NsBetaStrategy(BaseStrategy):
    """参数命名空间测试：与 NsAlpha 同名参数 ``threshold``。"""

    strategy_id = "test_ns_beta"
    label = "命名空间乙"
    version = "1.0.0"
    description = "命名空间测试"
    phases = frozenset({Phase.POOL})
    params_schema = (THRESHOLD_SPEC,)


# ============================================================ fixtures


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    """每个用例前后清空注册表，保证用例互不干扰。"""
    clear_registry()
    yield
    clear_registry()


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


def _plugin_source(strategy_id: str, class_name: str = "Demo") -> str:
    """生成一个最小临时插件源码（一个包 = 一个策略）。"""
    return (
        "from app.strategies.protocol import BaseStrategy, CycleState, GateRule, Phase\n"
        "\n"
        f"class {class_name}(BaseStrategy):\n"
        f'    strategy_id = "{strategy_id}"\n'
        '    label = "临时插件"\n'
        '    version = "1.0.0"\n'
        '    description = "临时插件"\n'
        "    phases = frozenset({Phase.POOL})\n"
        "    gate_matrix = {CycleState.REPAIR: GateRule(True, 0.5)}\n"
    )


def _write_pkg(root: Path, name: str, source: str) -> Path:
    """在 ``root`` 下写入名为 ``name`` 的插件包。"""
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(source, encoding="utf-8")
    return pkg


# ============================================================ 1. 自动发现


def test_discovery_finds_plugin_in_directory(tmp_path: Path) -> None:
    """临时目录中的插件包被自动发现并注册。"""
    package = f"strategy_test_{uuid.uuid4().hex}"
    _write_pkg(tmp_path, "demo", _plugin_source("tmp_demo_plugin"))

    result = discover_plugins(directory=tmp_path, package=package, strict=True)

    assert result.errors == ()
    assert result.modules == (f"{package}.demo",)
    assert [cls.strategy_id for cls in result.strategies] == ["tmp_demo_plugin"]
    assert get_strategy("tmp_demo_plugin").label == "临时插件"
    assert "tmp_demo_plugin" in {cls.strategy_id for cls in all_strategies()}


def test_duplicate_strategy_id_raises_naming_both_modules(tmp_path: Path) -> None:
    """重复 strategy_id 在发现时直接报错，且错误信息同时点出两个模块。"""
    package = f"strategy_test_{uuid.uuid4().hex}"
    _write_pkg(tmp_path, "a", _plugin_source("dup_plugin"))
    _write_pkg(tmp_path, "b", _plugin_source("dup_plugin"))

    with pytest.raises(StrategyRegistryError) as excinfo:
        discover_plugins(directory=tmp_path, package=package)

    message = str(excinfo.value)
    assert "dup_plugin" in message
    assert f"{package}.a" in message
    assert f"{package}.b" in message


def test_broken_plugin_reported_without_crashing(tmp_path: Path) -> None:
    """坏插件被报告为错误且不中断其余插件；strict=True 时直接抛出。"""
    package = f"strategy_test_{uuid.uuid4().hex}"
    _write_pkg(tmp_path, "broken", 'raise RuntimeError("插件炸了")\n')
    _write_pkg(tmp_path, "good", _plugin_source("tmp_good_plugin"))

    result = discover_plugins(directory=tmp_path, package=package)

    assert [cls.strategy_id for cls in result.strategies] == ["tmp_good_plugin"]
    assert len(result.errors) == 1
    assert result.errors[0].module == f"{package}.broken"
    assert "RuntimeError" in result.errors[0].error
    assert f"{package}.good" in result.modules

    # strict=True：启动时快速失败
    with pytest.raises(RuntimeError):
        discover_plugins(directory=tmp_path, package=package, strict=True)


# ============================================================ 2. 门控来自策略声明


def test_gate_reads_strategy_own_matrix(caplog: pytest.LogCaptureFixture) -> None:
    """evaluate_gate 只读策略自身声明；未声明态告警并用文档化默认（不静默禁用）。"""
    register_strategy(GateDemoStrategy)

    declared_ok = evaluate_gate(GateDemoStrategy, CycleState.REPAIR)
    assert (declared_ok.allowed, declared_ok.position_factor) == (True, 0.42)
    assert declared_ok.state is CycleState.REPAIR

    declared_no = evaluate_gate(GateDemoStrategy, CycleState.RETREAT)
    assert (declared_no.allowed, declared_no.position_factor) == (False, 0.0)

    # 未声明的态（ACCEL）与降级态（UNKNOWN）→ 告警 + 默认放行，绝不静默禁用
    with caplog.at_level(logging.WARNING, logger="app.strategies.registry"):
        for state in (CycleState.ACCEL, CycleState.UNKNOWN):
            decision = evaluate_gate(GateDemoStrategy, state)
            assert decision.allowed is True
            assert decision.position_factor == DEFAULT_GATE_POSITION_FACTOR
            assert "默认" in decision.reason
    fallbacks = [r for r in caplog.records if r.msg == "strategy_gate_fallback"]
    assert len(fallbacks) == 2
    assert fallbacks[0].state == CycleState.ACCEL.value  # type: ignore[attr-defined]


def test_gate_needs_no_core_edit() -> None:
    """门控完全由声明驱动：核心文件中不含任何测试策略标识/标签。"""
    register_strategy(GateDemoStrategy)
    assert evaluate_gate(GateDemoStrategy, CycleState.REPAIR).position_factor == 0.42

    core_dir = Path(registry_module.__file__).resolve().parent
    for name in ("protocol.py", "context.py", "registry.py", "loader.py"):
        text = (core_dir / name).read_text(encoding="utf-8")
        assert "test_gate_demo" not in text, f"{name} 硬编码了策略标识"
        assert "测试门控" not in text, f"{name} 硬编码了策略标签"


# ============================================================ 3. 隔离执行


async def test_run_phase_isolates_failures(
    repos: Repositories, session: AsyncSession
) -> None:
    """中间策略抛异常：其余策略仍产出结果，失败被记录，汇总为 1 失败 / 2 成功。"""
    for cls in (AlphaStrategy, BoomStrategy, GammaStrategy):
        register_strategy(cls)

    factory = StrategyContextFactory(repos=repos, settings=get_settings(), trade_date=TRADE_DATE)
    summary = await run_phase(Phase.POOL, factory, repos)

    assert summary.success_count == 2
    assert summary.failure_count == 1
    assert summary.failed_ids == ["test_boom"]
    assert summary.succeeded_ids == ["test_alpha", "test_gamma"]
    assert [item.output for item in summary.results if item.ok] == [
        {"strategy": "test_alpha"},
        "test_gamma",
    ]

    # 失败以 kind="error" 的建议报告落库，携带 strategy_id
    errors = await repos.advice_reports.get_by_date(TRADE_DATE, kind="error")
    assert [row.strategy_id for row in errors] == ["test_boom"]
    assert errors[0].payload["phase"] == Phase.POOL.value
    assert "RuntimeError" in errors[0].payload["error_type"]


async def test_strategies_with_phase_filters_by_phase(repos: Repositories) -> None:
    """阶段筛选：仅参与该阶段的策略被返回。"""
    register_strategy(AlphaStrategy)
    register_strategy(IntradayOnlyStrategy)

    pool = await strategies_with_phase(Phase.POOL, repos)
    intraday = await strategies_with_phase(Phase.INTRADAY, repos)

    assert [cls.strategy_id for cls in pool] == ["test_alpha"]
    assert [cls.strategy_id for cls in intraday] == ["test_intraday_only"]


# ============================================================ 4. 参数命名空间


async def test_params_namespacing_prevents_collision(
    repos: Repositories, session: AsyncSession
) -> None:
    """两个策略都定义 threshold，各自解析到自己的值（互不串台）。"""
    register_strategy(NsAlphaStrategy)
    register_strategy(NsBetaStrategy)

    draft_a = await repos.strategy_configs.create_draft("test_ns_alpha", {"threshold": 0.1})
    await repos.strategy_configs.activate("test_ns_alpha", int(draft_a.version))
    draft_b = await repos.strategy_configs.create_draft("test_ns_beta", {"threshold": 0.9})
    await repos.strategy_configs.activate("test_ns_beta", int(draft_b.version))
    await session.commit()

    assert (await resolve_params(NsAlphaStrategy, repos)).params["threshold"] == pytest.approx(0.1)
    assert (await resolve_params(NsBetaStrategy, repos)).params["threshold"] == pytest.approx(0.9)

    # 覆盖甲不影响乙
    with override_params("test_ns_alpha", {"threshold": 0.7}):
        assert (await resolve_params(NsAlphaStrategy, repos)).params["threshold"] == pytest.approx(
            0.7
        )
        assert (await resolve_params(NsBetaStrategy, repos)).params["threshold"] == pytest.approx(
            0.9
        )


# ============================================================ 5. 解析顺序 / 并发隔离


async def test_params_resolution_order_and_invalid_fallback(
    repos: Repositories, session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """解析顺序：覆盖 > active 配置 > 代码默认；非法值回退默认并告警。"""
    register_strategy(NsAlphaStrategy)

    # 代码默认
    assert (await resolve_params(NsAlphaStrategy)).params["threshold"] == pytest.approx(0.5)

    draft = await repos.strategy_configs.create_draft("test_ns_alpha", {"threshold": 0.2})
    await repos.strategy_configs.activate("test_ns_alpha", int(draft.version))
    await session.commit()

    active = await resolve_params(NsAlphaStrategy, repos)
    assert active.params["threshold"] == pytest.approx(0.2)
    assert active.source == "active"

    with override_params("test_ns_alpha", {"threshold": 0.3}):
        overridden = await resolve_params(NsAlphaStrategy, repos)
    assert overridden.params["threshold"] == pytest.approx(0.3)
    assert overridden.source == "override"

    # 退出覆盖后回到 active
    assert (await resolve_params(NsAlphaStrategy, repos)).params["threshold"] == pytest.approx(0.2)

    # 非法值（超出 max=1.0）→ 回退代码默认 + 结构化告警
    with (
        override_params("test_ns_alpha", {"threshold": 5.0}),
        caplog.at_level(logging.WARNING, logger="app.strategies.protocol"),
    ):
        invalid = await resolve_params(NsAlphaStrategy, repos)
    assert invalid.params["threshold"] == pytest.approx(0.5)
    warnings = [r for r in caplog.records if r.msg == "strategy_param_invalid"]
    assert warnings
    assert warnings[0].strategy_id == "test_ns_alpha"  # type: ignore[attr-defined]
    assert warnings[0].fallback == 0.5  # type: ignore[attr-defined]


async def test_override_isolation_across_concurrent_tasks() -> None:
    """并发任务各自的覆盖互不可见（ContextVar 隔离，回归旧全局 dict 缺陷）。"""
    register_strategy(NsAlphaStrategy)
    seen: dict[str, float] = {}

    async def worker(name: str, value: float) -> None:
        with override_params("test_ns_alpha", {"threshold": value}):
            await asyncio.sleep(0)  # 让出控制权，制造交错
            first = (await resolve_params(NsAlphaStrategy)).params["threshold"]
            await asyncio.sleep(0)
            second = (await resolve_params(NsAlphaStrategy)).params["threshold"]
            seen[name] = first
            assert first == pytest.approx(value)
            assert second == pytest.approx(value)

    await asyncio.gather(worker("a", 0.11), worker("b", 0.22))

    assert seen == {"a": pytest.approx(0.11), "b": pytest.approx(0.22)}
    assert current_overrides() == {}
    assert (await resolve_params(NsAlphaStrategy)).params["threshold"] == pytest.approx(0.5)


# ============================================================ 6. 启用 / 停用


async def test_disabled_strategy_skipped(repos: Repositories, session: AsyncSession) -> None:
    """停用的策略被 run_phase 与 strategies_with_phase 同时跳过。"""
    register_strategy(AlphaStrategy)
    register_strategy(GammaStrategy)
    await sync_definitions(repos)
    await session.commit()

    assert await set_enabled("test_alpha", False, repos)
    await session.commit()

    selected = await strategies_with_phase(Phase.POOL, repos)
    assert [cls.strategy_id for cls in selected] == ["test_gamma"]

    factory = StrategyContextFactory(repos=repos, settings=get_settings(), trade_date=TRADE_DATE)
    summary = await run_phase(Phase.POOL, factory, repos)
    assert summary.succeeded_ids == ["test_gamma"]
    assert "test_alpha" not in [item.strategy_id for item in summary.results]


# ============================================================ 7. 架构测试


def test_plugins_do_not_import_repositories_or_datasources() -> None:
    """插件/示例代码禁止 import 仓储、数据源或具体 provider。"""
    import re

    strategies_dir = Path(registry_module.__file__).resolve().parent
    banned = [
        r"^\s*import\s+app\.repositories\b",
        r"^\s*from\s+app\.repositories\b",
        r"^\s*import\s+app\.datasources\b",
        r"^\s*from\s+app\.datasources\b",
        r"^\s*from\s+app\.datasources\.providers\b",
        r"^\s*import\s+app\.datasources\.providers\b",
    ]
    offenders: list[str] = []
    for root_name in ("plugins", "examples"):
        for path in sorted((strategies_dir / root_name).rglob("*.py")):
            code = path.read_text(encoding="utf-8")
            for pattern in banned:
                if re.search(pattern, code, flags=re.MULTILINE):
                    offenders.append(f"{path.relative_to(strategies_dir)}: {pattern}")
    assert not offenders, "插件/示例出现禁止的依赖：\n" + "\n".join(offenders)


# ============================================================ 8. 定义同步


async def test_sync_definitions_upserts_and_is_idempotent(
    repos: Repositories, session: AsyncSession
) -> None:
    """sync_definitions 幂等 upsert 策略定义（含参数 schema 与门控矩阵）。"""
    register_strategy(AlphaStrategy)
    register_strategy(GammaStrategy)

    synced = await sync_definitions(repos)
    await session.commit()
    assert synced == ["test_alpha", "test_gamma"]

    rows = await repos.strategy_defs.list_all()
    assert {row.strategy_id for row in rows} == {"test_alpha", "test_gamma"}
    alpha = next(row for row in rows if row.strategy_id == "test_alpha")
    assert {spec["key"] for spec in alpha.params_schema["params"]} == {"threshold"}
    assert alpha.gate_matrix[CycleState.REPAIR.value] == {"allowed": True, "position_factor": 0.5}

    # 重新同步：行数不变，且不覆盖停用状态
    await set_enabled("test_alpha", False, repos)
    await sync_definitions(repos)
    await session.commit()
    rows = await repos.strategy_defs.list_all()
    assert len(rows) == 2
    assert next(row for row in rows if row.strategy_id == "test_alpha").enabled is False


# ============================================================ 附加：schema / 周期态


def test_export_schemas_and_cycle_states() -> None:
    """schema 可导出；周期态含六态 + 降级态。"""
    register_strategy(GateDemoStrategy)
    schemas = export_schemas()
    assert [item["strategy_id"] for item in schemas] == ["test_gate_demo"]
    assert schemas[0]["gate_matrix"][CycleState.REPAIR.value]["position_factor"] == 0.42

    canonical = {
        CycleState.ICE.value,
        CycleState.TURN.value,
        CycleState.REPAIR.value,
        CycleState.ACCEL.value,
        CycleState.DIVERGE.value,
        CycleState.RETREAT.value,
    }
    assert canonical == {"冰点", "冰点转折", "修复", "加速/高潮", "分歧", "退潮"}
    assert CycleState.UNKNOWN.value == "未知"
    assert protocol_module.Phase.POOL.value == "pool"
