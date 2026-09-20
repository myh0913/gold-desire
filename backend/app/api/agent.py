"""内置 Agent 路由：会话、消息、SSE 流式对话、危险操作确认、工具与技能清单。

权限约定：

- 全部端点要求已登录；会话**属主校验在服务端强制**（非属主 403，不存在 404）。
- ``GET /agent/tools`` 按调用者角色过滤，**不向非 admin 泄露任何变更工具名**。
- 对话为 SSE（``text/event-stream``），事件类型见 :mod:`app.agent.loop`。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from app.agent.client import LLMClient
from app.agent.loop import EVENT_DONE, EVENT_ERROR, AgentEvent
from app.agent.permissions import available_tools
from app.agent.skills import get_skill, list_skills
from app.api.deps import get_current_user
from app.core.cache import CacheBackend, get_cache
from app.core.config import Settings, get_settings
from app.models.auth import User
from app.repositories import Repositories, get_repositories
from app.schemas.agent import (
    AgentChatRequest,
    AgentConfirmRequest,
    AgentConfirmResponse,
    AgentMessageOut,
    AgentMessagesResponse,
    AgentSessionCreate,
    AgentSessionOut,
    AgentSessionsResponse,
    AgentSkillOut,
    AgentSkillsResponse,
    AgentToolOut,
    AgentToolsResponse,
)
from app.services.agent_service import AgentService, build_llm_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])

#: SSE 响应头（禁用中间层缓冲，保证逐事件下发）。
_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


def get_agent_service(
    repos: Repositories = Depends(get_repositories),
    settings: Settings = Depends(get_settings),
    cache: CacheBackend = Depends(get_cache),
) -> AgentService:
    """请求级 Agent 服务。"""
    return AgentService(repos, settings=settings, cache=cache)


def get_llm_client(settings: Settings = Depends(get_settings)) -> LLMClient:
    """请求级模型客户端（测试可覆盖为脚本化假客户端，保证零网络）。"""
    return build_llm_client(settings)


# ------------------------------------------------------------------ 会话


@router.post(
    "/agent/sessions", response_model=AgentSessionOut, status_code=status.HTTP_201_CREATED
)
async def create_session(
    payload: AgentSessionCreate,
    user: User = Depends(get_current_user),
    service: AgentService = Depends(get_agent_service),
) -> AgentSessionOut:
    """创建当前用户的 Agent 会话。"""
    row = await service.create_session(user, payload.title)
    return AgentSessionOut.model_validate(row)


@router.get("/agent/sessions", response_model=AgentSessionsResponse)
async def list_sessions(
    user: User = Depends(get_current_user),
    service: AgentService = Depends(get_agent_service),
) -> AgentSessionsResponse:
    """列出当前用户的会话。"""
    rows = await service.list_sessions(user)
    return AgentSessionsResponse(items=[AgentSessionOut.model_validate(row) for row in rows])


@router.get("/agent/sessions/{session_id}/messages", response_model=AgentMessagesResponse)
async def get_messages(
    session_id: str,
    user: User = Depends(get_current_user),
    service: AgentService = Depends(get_agent_service),
) -> AgentMessagesResponse:
    """取会话消息历史（须为会话属主）。"""
    rows = await service.messages(session_id, user)
    return AgentMessagesResponse(
        session_id=session_id, items=[AgentMessageOut.model_validate(row) for row in rows]
    )


# ------------------------------------------------------------------ 对话


@router.post("/agent/sessions/{session_id}/chat")
async def chat(
    session_id: str,
    payload: AgentChatRequest,
    user: User = Depends(get_current_user),
    service: AgentService = Depends(get_agent_service),
    client: LLMClient = Depends(get_llm_client),
) -> StreamingResponse:
    """SSE 流式对话：返回 ``token`` / ``tool_call_start`` / ``tool_call_result`` /
    ``confirmation_required`` / ``error`` / ``done`` 事件。

    所有权与技能名在流开始前校验，故 403/404 仍以普通 HTTP 错误返回。
    """
    await service.require_owner(session_id, user)
    skill = get_skill(payload.skill) if payload.skill else None

    async def _events() -> AsyncIterator[str]:
        try:
            async for event in service.stream_chat(
                session_id, user, payload.message, skill=skill, client=client
            ):
                yield event.to_sse()
        except Exception:  # pragma: no cover - 防御性：绝不让流挂起
            logger.exception("agent_stream_failed", extra={"session_id": session_id})
            yield AgentEvent(
                EVENT_ERROR, {"code": "agent_internal_error", "message": "Agent 内部错误"}
            ).to_sse()
            yield AgentEvent(EVENT_DONE, {"reason": "error"}).to_sse()

    return StreamingResponse(_events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/agent/sessions/{session_id}/confirm", response_model=AgentConfirmResponse)
async def confirm(
    session_id: str,
    payload: AgentConfirmRequest,
    user: User = Depends(get_current_user),
    service: AgentService = Depends(get_agent_service),
) -> AgentConfirmResponse:
    """确认并执行一条待人工确认的危险操作（令牌绑定会话 + 用户，一次性）。"""
    result = await service.confirm(session_id, user, payload.token)
    return AgentConfirmResponse.model_validate(result)


# ------------------------------------------------------------------ 清单


@router.get("/agent/tools", response_model=AgentToolsResponse)
async def list_tools(user: User = Depends(get_current_user)) -> AgentToolsResponse:
    """当前角色可用的工具清单（非 admin 不含任何变更工具）。"""
    return AgentToolsResponse(
        items=[AgentToolOut(**tool.public_dict()) for tool in available_tools(user)]
    )


@router.get("/agent/skills", response_model=AgentSkillsResponse)
async def list_agent_skills(user: User = Depends(get_current_user)) -> AgentSkillsResponse:
    """预置技能清单（对任意已登录用户开放）。"""
    return AgentSkillsResponse(
        items=[AgentSkillOut(**skill.to_dict()) for skill in list_skills()]
    )


__all__ = ["get_agent_service", "get_llm_client", "router"]
