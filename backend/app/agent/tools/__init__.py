"""Agent 工具包：导入即完成全部工具注册。

- :mod:`app.agent.tools.base`：工具协议、注册表与执行上下文；
- :mod:`app.agent.tools.read`：只读工具（所有已认证角色可用）；
- :mod:`app.agent.tools.market_query`：只读行情工具（自 read 拆出，所有已认证角色可用）；
- :mod:`app.agent.tools.mutate`：变更工具（仅 admin）；
- :mod:`app.agent.tools.query_table`：白名单表的结构化查询工具。

**注意**：任何需要工具注册表的模块都应 ``from app.agent.tools import ...``（本包），
而不是直接从 ``base`` 导入，否则注册表可能为空。
"""

from __future__ import annotations

from app.agent.tools import market_query, mutate, query_table, read  # noqa: F401  （导入即注册）
from app.agent.tools.base import (
    AgentTool,
    AuditHook,
    ToolArgumentError,
    ToolContext,
    ToolError,
    ToolRegistryError,
    all_tools,
    build_tool_context,
    get_tool,
    register_tool,
    to_openai_tools,
    tool_schema,
)
from app.agent.tools.query_table import TABLE_WHITELIST, QueryTableTool

__all__ = [
    "TABLE_WHITELIST",
    "AgentTool",
    "AuditHook",
    "QueryTableTool",
    "ToolArgumentError",
    "ToolContext",
    "ToolError",
    "ToolRegistryError",
    "all_tools",
    "build_tool_context",
    "get_tool",
    "register_tool",
    "to_openai_tools",
    "tool_schema",
]
