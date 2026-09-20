"""数据源注册表：provider 注册、能力→有序源列表、启动一致性断言。

- ``register_provider`` 装饰器在导入 provider 模块时完成注册，并即时校验
  「声明能力必须已有契约」；
- ``CAPABILITY_PROVIDERS`` 维护能力 → 有序源列表（主源在前），运行时可被
  管理侧覆盖（``set_capability_order``，Task 4/12 落库持久化）；
- ``assert_registry_consistent`` 在启动时检查：能力有契约、有序源已注册且有映射。
"""

from __future__ import annotations

from app.datasources.base import BaseProvider, ProviderMeta
from app.datasources.contracts import CAPABILITY_CONTRACTS
from app.datasources.mappings.registry import all_mappings

__all__ = [
    "CAPABILITY_PROVIDERS",
    "RegistryError",
    "all_providers",
    "assert_registry_consistent",
    "get_provider",
    "provider_metas",
    "register_provider",
    "resolve_order",
    "set_capability_order",
]


class RegistryError(RuntimeError):
    """注册表不一致（能力无契约 / 有序源未注册或缺映射）。"""


_PROVIDERS: dict[str, type[BaseProvider]] = {}

#: 能力 → 有序源 id 列表（主源在前）。默认全量指向 fake，真实源在 Task 6 接入后调整。
CAPABILITY_PROVIDERS: dict[str, list[str]] = {
    capability: ["fake"] for capability in CAPABILITY_CONTRACTS
}

#: 运行时覆盖（管理侧主备调整），优先于 CAPABILITY_PROVIDERS。
_CAPABILITY_OVERRIDES: dict[str, list[str]] = {}


def register_provider(cls: type[BaseProvider]) -> type[BaseProvider]:
    """注册 provider 类（装饰器用法）。

    Raises:
        RegistryError: 缺少 ``source_id``、未声明能力，或声明了无契约的能力。
    """
    if not cls.source_id:
        raise RegistryError(f"{cls.__name__} 缺少 source_id")
    if not cls.capabilities:
        raise RegistryError(f"{cls.__name__} 未声明任何 capabilities")
    unknown = [cap for cap in cls.capabilities if cap not in CAPABILITY_CONTRACTS]
    if unknown:
        raise RegistryError(
            f"{cls.source_id} 声明了无契约的能力 {unknown}；"
            f"请先在 contracts 中定义（已有：{sorted(CAPABILITY_CONTRACTS)}）"
        )
    _PROVIDERS[cls.source_id] = cls
    return cls


def get_provider(source_id: str) -> type[BaseProvider]:
    """取 provider 类。

    Raises:
        RegistryError: 未注册。
    """
    try:
        return _PROVIDERS[source_id]
    except KeyError as exc:
        raise RegistryError(
            f"未注册的数据源: {source_id!r}（已注册：{sorted(_PROVIDERS)}）"
        ) from exc


def all_providers() -> list[type[BaseProvider]]:
    """返回全部已注册 provider 类（按 priority、source_id 排序）。"""
    return sorted(_PROVIDERS.values(), key=lambda cls: (cls.priority, cls.source_id))


def provider_metas() -> list[ProviderMeta]:
    """返回全部 provider 元信息。"""
    return [cls.meta() for cls in all_providers()]


def resolve_order(capability: str) -> list[str]:
    """返回该能力的取数顺序（主源在前）；运行时覆盖优先于默认。"""
    if capability in _CAPABILITY_OVERRIDES:
        return list(_CAPABILITY_OVERRIDES[capability])
    return list(CAPABILITY_PROVIDERS.get(capability, []))


def set_capability_order(capability: str, source_ids: list[str]) -> None:
    """设置能力 → 有序源列表的运行时覆盖（Task 4/12 持久化后调用）。

    Raises:
        RegistryError: 引用了未注册的数据源。
    """
    unknown = [sid for sid in source_ids if sid not in _PROVIDERS]
    if unknown:
        raise RegistryError(f"能力 {capability!r} 引用未注册数据源 {unknown}")
    _CAPABILITY_OVERRIDES[capability] = list(source_ids)


def reset_capability_order(capability: str | None = None) -> None:
    """清除运行时覆盖（缺省清空全部）；仅供测试与管理侧回滚使用。"""
    if capability is None:
        _CAPABILITY_OVERRIDES.clear()
    else:
        _CAPABILITY_OVERRIDES.pop(capability, None)


def assert_registry_consistent() -> None:
    """启动断言：注册表自洽性检查。

    - 每个 provider 声明的能力都必须有契约（注册时已保证，这里再兜底）；
    - ``CAPABILITY_PROVIDERS`` 中的每个能力都必须有契约；
    - 每个能力列出的源必须已注册，且该 (源, 能力) 已有映射。

    Raises:
        RegistryError: 任一检查不通过（错误信息列出全部问题）。
    """
    problems: list[str] = []

    for cls in _PROVIDERS.values():
        for capability in cls.capabilities:
            if capability not in CAPABILITY_CONTRACTS:
                problems.append(f"provider {cls.source_id} 的能力 {capability!r} 无契约")

    registered = set(all_mappings())
    for capability, source_ids in CAPABILITY_PROVIDERS.items():
        if capability not in CAPABILITY_CONTRACTS:
            problems.append(f"CAPABILITY_PROVIDERS 能力 {capability!r} 无契约")
        for source_id in source_ids:
            if source_id not in _PROVIDERS:
                problems.append(f"能力 {capability!r} 引用未注册数据源 {source_id!r}")
                continue
            if (source_id, capability) not in registered:
                problems.append(f"能力 {capability!r} 的源 {source_id!r} 缺少字段映射")

    if problems:
        raise RegistryError("数据源注册表不一致：\n" + "\n".join(f"- {item}" for item in problems))
