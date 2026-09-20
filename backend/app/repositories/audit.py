"""审计仓储：通用审计日志与 Agent 会话/消息/工具调用全量留痕。

对应 spec「审计可追溯」：可通过 ``agent_sessions`` / ``agent_messages`` /
``agent_tool_calls`` 还原「谁、何时、哪个会话、调用了哪个工具、入参、结果如何」。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.models.auth import AuditLog
from app.models.derived import AgentMessage, AgentSession, AgentToolCall
from app.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository):
    """通用审计日志仓储（写操作、登录/登出、权限变更、Agent 工具调用）。"""

    async def record(
        self,
        actor: str | None,
        action: str,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
        ip: str | None = None,
        request_id: str | None = None,
    ) -> AuditLog:
        """追加一条审计日志并返回该行。"""
        row = AuditLog(
            actor=actor,
            action=action,
            target=target,
            detail=detail,
            ip=ip,
            request_id=request_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_recent(
        self, limit: int = 50, actor: str | None = None, action: str | None = None
    ) -> list[AuditLog]:
        """按时间倒序取审计日志，可按操作者/动作过滤。"""
        stmt = select(AuditLog)
        if actor is not None:
            stmt = stmt.where(AuditLog.actor == actor)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        stmt = stmt.order_by(AuditLog.ts.desc()).limit(max(1, limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_range(self, start: datetime, end: datetime) -> list[AuditLog]:
        """取时间落在 ``[start, end]`` 的审计日志，按时间升序。"""
        stmt = select(AuditLog).where(AuditLog.ts.between(start, end)).order_by(AuditLog.ts)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class AgentSessionRepository(BaseRepository):
    """Agent 会话仓储。"""

    async def create(self, session_id: str, user_id: str, title: str | None = None) -> AgentSession:
        """新建 Agent 会话并返回该行。"""
        row = AgentSession(session_id=session_id, user_id=user_id, title=title)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, session_id: str) -> AgentSession | None:
        """按 ``session_id`` 取会话。"""
        stmt = select(AgentSession).where(AgentSession.session_id == session_id)
        return await self.session.scalar(stmt)

    async def list_by_user(self, user_id: str, limit: int = 50) -> list[AgentSession]:
        """列出某用户的会话，按创建时间倒序。"""
        stmt = (
            select(AgentSession)
            .where(AgentSession.user_id == user_id)
            .order_by(AgentSession.id.desc())
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class AgentMessageRepository(BaseRepository):
    """Agent 会话消息仓储。"""

    async def append(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tokens: int = 0,
    ) -> AgentMessage:
        """追加一条会话消息并返回该行。"""
        row = AgentMessage(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tokens=tokens,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_session(self, session_id: str, limit: int = 200) -> list[AgentMessage]:
        """按时间升序列出某会话的消息。"""
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.id)
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class AgentToolCallRepository(BaseRepository):
    """Agent 工具调用审计仓储。"""

    async def record(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ok: bool = True,
        denied: bool = False,
        duration_ms: int | None = None,
        result_summary: str | None = None,
    ) -> AgentToolCall:
        """记录一次工具调用（入参、结果摘要、耗时、是否被拒）并返回该行。"""
        row = AgentToolCall(
            session_id=session_id,
            tool_name=tool_name,
            arguments=arguments,
            result_summary=result_summary,
            ok=ok,
            denied=denied,
            duration_ms=duration_ms,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_session(self, session_id: str, limit: int = 200) -> list[AgentToolCall]:
        """按时间升序列出某会话的工具调用。"""
        stmt = (
            select(AgentToolCall)
            .where(AgentToolCall.session_id == session_id)
            .order_by(AgentToolCall.id)
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


__all__ = [
    "AgentMessageRepository",
    "AgentSessionRepository",
    "AgentToolCallRepository",
    "AuditLogRepository",
]
