"""映射注册表：``register_mapping`` / ``get_mapping`` / ``all_mappings``。

以 ``(source_id, capability)`` 为键。Python 注册（``defs/*.py``）与 YAML 注册
（``defs/*.yaml``）共用本表；冲突时 **Python 优先**（见 ``loader``）。
"""

from __future__ import annotations

from app.datasources.contracts import CAPABILITY_CONTRACTS
from app.datasources.mappings.dsl import CapabilityMapping, MappingError

__all__ = ["all_mappings", "clear_mappings", "get_mapping", "register_mapping"]

_MAPPINGS: dict[tuple[str, str], CapabilityMapping] = {}


def register_mapping(mapping: CapabilityMapping, *, replace: bool = False) -> CapabilityMapping:
    """注册一份 (源, 能力) 映射。

    Args:
        mapping: 声明式映射。
        replace: 冲突时是否覆盖已有映射；默认 ``False``（先注册者优先，
            从而使 Python 注册的映射不被 YAML 覆盖）。

    Raises:
        MappingError: 能力未在 ``CAPABILITY_CONTRACTS`` 中声明。
    """
    if mapping.capability not in CAPABILITY_CONTRACTS:
        raise MappingError(
            f"能力 {mapping.capability!r} 未声明契约，无法为 {mapping.source_id!r} 注册映射"
        )
    key = (mapping.source_id, mapping.capability)
    if key in _MAPPINGS and not replace:
        return _MAPPINGS[key]
    _MAPPINGS[key] = mapping
    return mapping


def get_mapping(source_id: str, capability: str) -> CapabilityMapping:
    """取 (源, 能力) 映射。

    Raises:
        MappingError: 未注册。
    """
    try:
        return _MAPPINGS[(source_id, capability)]
    except KeyError as exc:
        raise MappingError(f"缺少映射：source={source_id!r} capability={capability!r}") from exc


def all_mappings() -> dict[tuple[str, str], CapabilityMapping]:
    """返回全部映射的浅拷贝（供启动断言/调试）。"""
    return dict(_MAPPINGS)


def clear_mappings() -> None:
    """清空注册表（仅供测试隔离使用）。"""
    _MAPPINGS.clear()
