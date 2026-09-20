"""因子注册表：schema 导出、定义同步与**参数解析**（配置驱动）。

参数解析顺序（高到低）：

1. **运行时覆盖**：``contextvars.ContextVar`` 中的覆盖值（并发任务相互隔离）；
2. **DB active 配置**：``factor_configs`` 中该因子的 ``active`` 版本；
3. **代码默认值**：因子 ``params_schema`` 声明的 ``default``。

任一来源的非法值都会回退到**代码默认值**，并发出结构化告警（``factor_param_invalid``）。

> 运行时覆盖刻意使用 :class:`contextvars.ContextVar` 而非模块级全局 dict——
> 后者在并发运行（多个 asyncio 任务 / 多线程）下会互相串台。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.factors.base import (
    BaseFactor,
    FactorParamSpec,
    all_factors,
    get_factor,
)

__all__ = [
    "FactorConfigRepo",
    "FactorDefRepo",
    "FactorRepos",
    "ResolvedParams",
    "current_overrides",
    "export_schemas",
    "get_params",
    "override_params",
    "resolve_params",
    "sync_definitions",
]

logger = logging.getLogger(__name__)

#: 运行时覆盖：``{factor_id: {param_key: value}}``；默认 ``None`` 表示无覆盖。
_OVERRIDES: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "factor_param_overrides", default=None
)


# ============================================================ 覆盖上下文


@contextmanager
def override_params(factor_id: str, params: Mapping[str, Any]) -> Iterator[None]:
    """在 ``with`` 块内覆盖某因子的参数（contextvars，任务间隔离）。

    嵌套使用时按 ``factor_id`` 合并，退出时逐层还原，不影响其他并发任务。

    Args:
        factor_id: 目标因子标识。
        params: ``{参数键: 值}``，仅覆盖列出的键。
    """
    current = dict(_OVERRIDES.get() or {})
    current[factor_id] = dict(params)
    token = _OVERRIDES.set(current)
    try:
        yield
    finally:
        _OVERRIDES.reset(token)


def current_overrides() -> dict[str, dict[str, Any]]:
    """返回当前上下文中的覆盖快照（拷贝，避免外部改动影响运行）。"""
    return {key: dict(value) for key, value in (_OVERRIDES.get() or {}).items()}


# ============================================================ 校验


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
    factor: BaseFactor,
    current: dict[str, Any],
    values: Mapping[str, Any],
    *,
    source: str,
) -> list[dict[str, Any]]:
    """把 ``values`` 校验后并入 ``current``，返回结构化告警列表。"""
    warnings: list[dict[str, Any]] = []
    for spec in factor.params_schema:
        if spec.key not in values:
            continue
        raw = values[spec.key]
        coerced, reason = _coerce(spec, raw)
        if reason is not None:
            warning = {
                "factor_id": factor.factor_id,
                "key": spec.key,
                "value": raw,
                "source": source,
                "reason": reason,
                "fallback": spec.default,
            }
            warnings.append(warning)
            logger.warning("factor_param_invalid", extra=warning)
        current[spec.key] = coerced
    return warnings


# ============================================================ 解析


@dataclass(frozen=True, slots=True)
class ResolvedParams:
    """参数解析结果。

    Attributes:
        factor_id: 因子标识。
        params: 生效参数。
        version: 参数版本串（``"default"`` / ``"v3"`` / ``"v3+override"``），
            供结果缓存拼键；版本变化即失效。
        source: 生效来源（``default`` / ``active`` / ``override``）。
        warnings: 非法值回退告警列表。
    """

    factor_id: str
    params: dict[str, Any]
    version: str
    source: str
    warnings: tuple[dict[str, Any], ...] = ()


async def resolve_params(
    factor_id: str,
    repos: FactorRepos | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> ResolvedParams:
    """按「覆盖 > active 配置 > 代码默认」解析因子参数。

    Args:
        factor_id: 因子标识。
        repos: 仓储容器；为 ``None`` 时跳过 DB（仅用覆盖与代码默认）。
        overrides: 显式覆盖；``None`` 时读取当前上下文（:func:`override_params`）。

    Raises:
        FactorRegistryError: 因子未注册。
    """
    factor: type[BaseFactor] = get_factor(factor_id)
    instance = factor()
    current = instance.default_params()
    version = "default"
    source = "default"
    warnings: list[dict[str, Any]] = []

    if repos is not None:
        active = await repos.factor_configs.get_active(factor_id)
        if active is not None:
            warnings.extend(_apply(instance, current, dict(active.params), source="active"))
            version = f"v{int(active.version)}"
            source = "active"

    effective = overrides if overrides is not None else current_overrides().get(factor_id)
    if effective:
        warnings.extend(_apply(instance, current, dict(effective), source="override"))
        version = f"{version}+override"
        source = "override"

    return ResolvedParams(
        factor_id=factor_id,
        params=current,
        version=version,
        source=source,
        warnings=tuple(warnings),
    )


async def get_params(
    factor_id: str,
    repos: FactorRepos | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """便捷入口：返回解析后的生效参数（见 :func:`resolve_params`）。"""
    return (await resolve_params(factor_id, repos, overrides=overrides)).params


# ============================================================ schema / 同步


def export_schemas() -> list[dict[str, Any]]:
    """导出全部因子的完整 schema（供前端自动渲染表单与 Agent 查询）。

    每项含 ``factor_id`` / ``label`` / ``category`` / ``description`` /
    ``params``（参数列表）与 ``buckets``（档位定义）。
    """
    return [factor().schema_dict() for factor in all_factors()]


@runtime_checkable
class FactorDefRepo(Protocol):
    """``FactorDefRepository`` 的结构化子集（避免因子层 import 仓储包）。"""

    async def upsert(
        self,
        factor_id: str,
        label: str,
        category: str,
        params_schema: dict[str, Any],
        *,
        description: str | None = None,
        enabled: bool = True,
    ) -> Any: ...


@runtime_checkable
class FactorConfigRepo(Protocol):
    """``FactorConfigRepository`` 的结构化子集。"""

    async def get_active(self, owner_id: str) -> Any | None: ...


@runtime_checkable
class FactorRepos(Protocol):
    """因子解析/同步所需的最小仓储视图（``Repositories`` 结构兼容）。"""

    factor_defs: FactorDefRepo
    factor_configs: FactorConfigRepo


async def sync_definitions(repos: FactorRepos) -> list[str]:
    """把全部已注册因子的定义幂等 upsert 进 ``factor_defs``（DB 始终反映代码）。

    Returns:
        已同步的 ``factor_id`` 列表（升序）。
    """
    synced: list[str] = []
    for factor in all_factors():
        instance = factor()
        await repos.factor_defs.upsert(
            factor.factor_id,
            factor.label,
            factor.category,
            instance.params_schema_dict(),
            description=factor.description,
            enabled=True,
        )
        synced.append(factor.factor_id)
    return synced
