"""内置 Agent 的请求/响应契约：会话、消息、工具清单、技能与人工确认。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentChatRequest",
    "AgentConfirmRequest",
    "AgentConfirmResponse",
    "AgentMessageOut",
    "AgentMessagesResponse",
    "AgentSessionCreate",
    "AgentSessionOut",
    "AgentSessionsResponse",
    "AgentSkillOut",
    "AgentSkillsResponse",
    "AgentToolOut",
    "AgentToolsResponse",
]

_ORM = ConfigDict(from_attributes=True)


class AgentSessionCreate(BaseModel):
    """创建会话请求。"""

    title: str | None = Field(default=None, max_length=255, description="会话标题")


class AgentSessionOut(BaseModel):
    """Agent 会话。"""

    model_config = _ORM

    session_id: str
    title: str | None = None
    created_at: datetime | None = None


class AgentSessionsResponse(BaseModel):
    """当前用户的会话列表。"""

    items: list[AgentSessionOut] = Field(default_factory=list)


class AgentMessageOut(BaseModel):
    """会话消息。"""

    model_config = _ORM

    id: int
    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tokens: int = 0
    created_at: datetime | None = None


class AgentMessagesResponse(BaseModel):
    """某会话的消息历史。"""

    session_id: str
    items: list[AgentMessageOut] = Field(default_factory=list)


class AgentChatRequest(BaseModel):
    """发起一轮对话（SSE 流式返回）。"""

    message: str = Field(min_length=1, max_length=8000, description="用户提问")
    skill: str | None = Field(default=None, max_length=64, description="可选技能名")


class AgentToolOut(BaseModel):
    """工具元数据（已按调用者角色过滤）。"""

    name: str
    description: str
    mutating: bool = False
    requires_confirmation: bool = False
    required_role: str | None = None


class AgentToolsResponse(BaseModel):
    """当前角色可用的工具清单。"""

    items: list[AgentToolOut] = Field(default_factory=list)


class AgentSkillOut(BaseModel):
    """技能元数据。"""

    name: str
    description: str
    tools: list[str] = Field(default_factory=list)
    output_format: str = "markdown"


class AgentSkillsResponse(BaseModel):
    """技能清单。"""

    items: list[AgentSkillOut] = Field(default_factory=list)


class AgentConfirmRequest(BaseModel):
    """确认一次待人工确认的危险操作。"""

    token: str = Field(min_length=1, max_length=128, description="确认令牌")


class AgentConfirmResponse(BaseModel):
    """危险操作确认后的执行结果。"""

    ok: bool = True
    tool: str
    result: Any = None
