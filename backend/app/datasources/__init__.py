"""上游数据源接入层（可插拔：能力契约 + 声明式映射）。

模块结构::

    contracts/   能力契约（领域标准名 + 显式单位），拒绝非法记录而非填 0
    mappings/    声明式字段映射 DSL + 转换注册表 + YAML 加载器 + defs/
    base.py      provider 协议、重试、按源令牌桶
    registry.py  provider 注册表、能力→有序源列表、启动一致性断言
    resolve.py   统一取数入口（只做映射 + 校验，无源分支）
    providers/   各具体数据源实现（业务代码禁止直接 import）

导入本包即完成内置 provider/映射注册并执行一致性断言。业务代码只应使用
``app.datasources.resolve.resolve`` / ``resolve_raw``。
"""

from __future__ import annotations

from app.datasources import contracts, mappings, providers, registry
from app.datasources.registry import assert_registry_consistent

mappings.load_builtin_mappings()
assert_registry_consistent()

__all__ = ["contracts", "mappings", "providers", "registry"]
