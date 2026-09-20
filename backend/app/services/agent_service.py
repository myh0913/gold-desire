"""内置 Agent 服务：会话管理、流式对话编排与危险操作确认执行。

- 会话所有权在服务端强制：非属主读/写一律 403，不存在的会话 404。
- 对话编排委托 :class:`~app.agent.loop.AgentLoop`（预算、鉴权、审计、事件流）。
- 确认执行走与循环**同一条**鉴权 + 工具执行路径，令牌一次性且绑定会话/用户。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.agent.client import LLMClient, OpenAICompatibleClient
from app.agent.loop import AgentEvent, AgentLoop
from app.agent.permissions import ConfirmationStore, authorize
from app.agent.skills import AgentSkill
from app.agent.tools import ToolContext, ToolError, ToolRegistryError, build_tool_context, get_tool
from app.core.cache import CacheBackend, get_cache
from app.core.config import Settings, get_settings
from app.core.errors import AppError, NotFoundError, PermissionDeniedError, ValidationError
from app.models.auth import User
from app.models.derived import AgentMessage, AgentSession
from app.repositories import Repositories

__all__ = ["AgentService", "build_llm_client"]

logger = logging.getLogger(__name__)


def build_llm_client(settings: Settings | None = None) -> LLMClient:
    """构造默认的 OpenAI 兼容客户端（测试通过依赖覆盖替换为脚本化假客户端）。"""
    return OpenAICompatibleClient(settings or get_settings())


def _summarize(payload: Any) -> str:
    """把结果压成定长摘要（供审计）。"""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return text if len(text) <= 500 else text[:497] + "..."


class AgentService:
    """Agent 会话与对话服务。"""

    def __init__(
        self,
        repos: Repositories,
        *,
        settings: Settings | None = None,
        cache: CacheBackend | None = None,
        client: LLMClient | None = None,
    ) -> None:
        self._repos = repos
        self._settings = settings or get_settings()
        self._cache = cache if cache is not None else get_cache()
        self._client = client

    # ------------------------------------------------------------------ 会话

    async def create_session(self, user: User, title: str | None = None) -> AgentSession:
        """新建一个属于 ``user`` 的会话。"""
        session_id = uuid.uuid4().hex
        row = await self._repos.agent_sessions.create(session_id, user.username, title)
        await self._repos.session.commit()
        return row

    async def list_sessions(self, user: User, limit: int = 50) -> list[AgentSession]:
        """列出当前用户的会话（按创建时间倒序）。"""
        return await self._repos.agent_sessions.list_by_user(user.username, limit)

    async def require_owner(self, session_id: str, user: User) -> AgentSession:
        """校验会话存在且属于 ``user``。

        Raises:
            NotFoundError: 会话不存在（404）。
            PermissionDeniedError: 会话属于其他用户（403）。
        """
        session = await self._repos.agent_sessions.get(session_id)
        if session is None:
            raise NotFoundError("会话不存在", detail={"session_id": session_id})
        if str(session.user_id) != user.username:
            raise PermissionDeniedError(
                "无权访问该会话", code="session_forbidden", detail={"session_id": session_id}
            )
        return session

    async def messages(self, session_id: str, user: User, limit: int = 200) -> list[AgentMessage]:
        """取会话消息历史（须为属主）。"""
        await self.require_owner(session_id, user)
        return await self._repos.agent_messages.list_session(session_id, limit)

    # ------------------------------------------------------------------ 对话

    async def stream_chat(
        self,
        session_id: str,
        user: User,
        message: str,
        *,
        skill: AgentSkill | None = None,
        client: LLMClient | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """流式执行一轮对话（所有权校验在流开始前完成）。"""
        await self.require_owner(session_id, user)
        loop = AgentLoop(
            client=client or self._resolve_client(),
            repos=self._repos,
            settings=self._settings,
            cache=self._cache,
            user=user,
            session_id=session_id,
        )
        async for event in loop.run(message, skill=skill):
            yield event

    def _resolve_client(self) -> LLMClient:
        """返回注入的客户端，缺省构造 OpenAI 兼容客户端。"""
        if self._client is None:
            self._client = build_llm_client(self._settings)
        return self._client

    # ------------------------------------------------------------------ 确认

    async def confirm(self, session_id: str, user: User, token: str) -> dict[str, Any]:
        """消费待确认令牌并执行危险操作。

        Raises:
            NotFoundError: 会话或工具不存在。
            PermissionDeniedError: 非属主、令牌无效/被他人使用、或角色已不再允许。
            ValidationError: 工具参数非法或执行失败。
        """
        await self.require_owner(session_id, user)
        pending = await ConfirmationStore(self._cache).take(
            token, session_id=session_id, user_id=user.username
        )
        try:
            tool = get_tool(pending.tool_name)
        except ToolRegistryError as exc:
            raise NotFoundError(
                f"工具不存在：{pending.tool_name}", detail={"tool": pending.tool_name}
            ) from exc
        await authorize(tool, user, repos=self._repos, session_id=session_id)

        ctx: ToolContext = build_tool_context(
            user=user,
            repos=self._repos,
            settings=self._settings,
            session_id=session_id,
            cache=self._cache,
        )
        started = time.perf_counter()
        try:
            result = await tool.execute(ctx, pending.arguments)
        except ToolError as exc:
            await self._record_confirmed(
                session_id, tool.name, pending.arguments, False, started, exc.to_payload()
            )
            raise ValidationError(exc.message, code=exc.code, detail=exc.detail) from exc
        except AppError as exc:
            await self._record_confirmed(
                session_id,
                tool.name,
                pending.arguments,
                False,
                started,
                {"error": {"code": exc.code, "message": exc.message}},
            )
            raise
        except Exception as exc:
            logger.exception("agent_confirm_failed", extra={"tool": tool.name})
            await self._record_confirmed(
                session_id,
                tool.name,
                pending.arguments,
                False,
                started,
                {"error": {"code": "tool_failed", "message": f"{type(exc).__name__}: {exc}"}},
            )
            raise ValidationError(f"工具执行失败：{type(exc).__name__}") from exc

        payload = {"ok": True, "result": result}
        await self._record_confirmed(
            session_id, tool.name, pending.arguments, True, started, payload
        )
        return {"ok": True, "tool": tool.name, "result": result}

    async def _record_confirmed(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        ok: bool,
        started: float,
        payload: Any,
    ) -> None:
        """记录确认后的工具调用审计与消息，并提交事务。"""
        duration_ms = int((time.perf_counter() - started) * 1000)
        summary = _summarize(payload)
        await self._repos.agent_tool_calls.record(
            session_id,
            tool_name,
            dict(arguments),
            ok=ok,
            denied=False,
            duration_ms=duration_ms,
            result_summary=summary,
        )
        await self._repos.agent_messages.append(
            session_id, "tool", json.dumps(payload, ensure_ascii=False, default=str)
        )
        await self._repos.session.commit()
