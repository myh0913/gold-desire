"""声明式字段映射层：DSL + 转换注册表 + 映射注册表 + YAML 加载器。

对外主入口::

    from app.datasources.mappings import CapabilityMapping, FieldMap, apply_mapping

导入本包会注册内置 Python 映射（``defs/fake.py``）。YAML 映射需显式调用
:func:`load_builtin_mappings`（或 ``loader.load_yaml_mappings``）加载。
"""

from __future__ import annotations

from app.datasources.mappings.dsl import (
    REQUIRED,
    CapabilityMapping,
    FieldMap,
    MappingError,
    apply_mapping,
)
from app.datasources.mappings.loader import DEFAULT_DEFS_DIR, load_yaml_mappings, parse_mapping_doc
from app.datasources.mappings.registry import (
    all_mappings,
    clear_mappings,
    get_mapping,
    register_mapping,
)
from app.datasources.mappings.transforms import (
    PARAM_TRANSFORMS,
    TRANSFORMS,
    TransformFn,
    resolve_transform,
)

__all__ = [
    "DEFAULT_DEFS_DIR",
    "PARAM_TRANSFORMS",
    "REQUIRED",
    "TRANSFORMS",
    "CapabilityMapping",
    "FieldMap",
    "MappingError",
    "TransformFn",
    "all_mappings",
    "apply_mapping",
    "clear_mappings",
    "get_mapping",
    "load_builtin_mappings",
    "load_yaml_mappings",
    "parse_mapping_doc",
    "register_mapping",
    "resolve_transform",
]


def load_builtin_mappings() -> None:
    """注册内置映射：Python（defs/*.py）优先，随后加载 YAML（不覆盖前者）。"""
    from app.datasources.mappings.defs import fake

    fake.register_fake_mappings()
    load_yaml_mappings(DEFAULT_DEFS_DIR)
