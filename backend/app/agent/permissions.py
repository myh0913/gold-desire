"""服务端强制的工具权限与危险操作人工确认（HITL）。

两层防护（spec：权限 SHALL 在服务端强制）：

1. **清单过滤**（:func:`available_tools`）：非 admin 的工具清单中**不含任何变更工具**，
   模型与前端都看不到它们（隐藏只是体验，不是安全边界）。
2. **执行前二次校验**（:func:`authorize`）：每次工具执行前按**服务端角色**再判一次；
   即使客户端构造了非法工具调用（绕过被过滤的清单），也会被拒绝并写入
   ``AuditLog(action="agent_tool_denied")``。

HITL：``requires_confirmation=True`` 的变更工具首轮不执行，改为在缓存中登记一条
**绑定会话 + 用户**的一次性令牌（TTL 有限、确认即消费），客户端经专用端点确认后
才真正执行——他人持令牌无法复用（会话/用户不匹配即拒绝）。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agent.tools import AgentTool, all_tools
from app.core.cache import CacheBackend
from app.core.errors import PermissionDeniedError
from app.models.auth import User
from app.repositories import Repositories

__all__ = [
    "ADMIN_ROLE",
    "CONFIRM_TTL_SECONDS",
    "ConfirmationStore",
    "PendingConfirmation",
    "authorize",
    "available_tools",
]

#: 管理员角色名（变更工具的唯一放行角色）。
ADMIN_ROLE = "admin"

#: 待确认令牌 TTL（秒）。
CONFIRM_TTL_SECONDS = 300

#: 待确认令牌缓存键前缀。
_CONFIRM_PREFIX = "agent:confirm"


def available_tools(user: User) -> list[AgentTool]:
    """返回该角色**可用**的工具清单（非 admin 零变更工具）。"""
    is_admin = user.role == ADMIN_ROLE
    allowed: list[AgentTool] = []
    for tool in all_tools():
        if tool.required_role is not None and user.role != tool.required_role:
            continue
        if tool.mutating and not is_admin:
            continue
        allowed.append(tool)
    return allowed


async def authorize(
    tool: AgentTool,
    user: User,
    *,
    repos: Repositories,
    session_id: str,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    """执行前的**第二次**角色校验；越权即拒绝并写审计。

    Raises:
        PermissionDeniedError: 角色不满足 ``required_role``，或非 admin 调用变更工具。
    """
    is_admin = user.role == ADMIN_ROLE
    denied = (tool.required_role is not None and user.role != tool.required_role) or (
        tool.mutating and not is_admin
    )
    if not denied:
        return
    await _record_denied(
        tool, user, repos=repos, session_id=session_id, ip=ip, request_id=request_id
    )
    raise PermissionDeniedError(
        f"无权调用工具：{tool.name}",
        code="agent_tool_denied",
        detail={"tool": tool.name, "role": user.role, "required_role": tool.required_role},
    )


async def _record_denied(
    tool: AgentTool,
    user: User,
    *,
    repos: Repositories,
    session_id: str,
    ip: str | None,
    request_id: str | None,
) -> None:
    """写越权审计并**立即提交**，避免后续异常导致回滚丢失证据。"""
    await repos.audit_logs.record(
        actor=user.username,
        action="agent_tool_denied",
        target=tool.name,
        detail={
            "session_id": session_id,
            "role": user.role,
            "required_role": tool.required_role,
            "mutating": tool.mutating,
        },
        ip=ip,
        request_id=request_id,
    )
    await repos.session.commit()


@dataclass(slots=True)
class PendingConfirmation:
    """一条待人工确认的危险操作。

    Attributes:
        token: 一次性令牌。
        session_id: 绑定的 Agent 会话。
        user_id: 绑定的用户名（令牌不可跨用户复用）。
        tool_name: 待执行工具名。
        arguments: 已校验的工具入参。
        created_at / expires_at: 创建与过期时间（ISO 串）。
    """

    token: str
    session_id: str
    user_id: str
    tool_name: str
    arguments: dict[str, Any]
    created_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        """序列化为缓存载荷。"""
        return {
            "token": self.token,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingConfirmation:
        """从缓存载荷还原。"""
        return cls(
            token=str(data["token"]),
            session_id=str(data["session_id"]),
            user_id=str(data["user_id"]),
            tool_name=str(data["tool_name"]),
            arguments=dict(data.get("arguments") or {}),
            created_at=str(data.get("created_at", "")),
            expires_at=str(data.get("expires_at", "")),
        )


class ConfirmationStore:
    """待确认令牌的缓存存储（TTL + 会话/用户绑定 + 一次性消费）。"""

    def __init__(self, cache: CacheBackend, *, ttl: int = CONFIRM_TTL_SECONDS) -> None:
        self._cache = cache
        self._ttl = max(1, ttl)

    @staticmethod
    def _key(token: str) -> str:
        """令牌缓存键。"""
        return f"{_CONFIRM_PREFIX}:{token}"

    async def create(
        self,
        *,
        session_id: str,
        user_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> PendingConfirmation:
        """登记一条待确认操作并返回其令牌。"""
        now = datetime.now(UTC)
        pending = PendingConfirmation(
            token=secrets.token_urlsafe(24),
            session_id=session_id,
            user_id=user_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=self._ttl)).isoformat(),
        )
        await self._cache.set(self._key(pending.token), pending.to_dict(), ttl=self._ttl)
        return pending

    async def take(
        self, token: str, *, session_id: str, user_id: str
    ) -> PendingConfirmation:
        """取出并**消费**令牌；绑定不匹配或不存在即拒绝。

        Raises:
            PermissionDeniedError: 令牌不存在/已过期，或不属于该会话/用户。
        """
        raw = await self._cache.get(self._key(token))
        if not isinstance(raw, dict):
            raise PermissionDeniedError(
                "确认令牌无效或已过期", code="confirmation_invalid", detail={"token": token[:8]}
            )
        pending = PendingConfirmation.from_dict(raw)
        if pending.session_id != session_id or pending.user_id != user_id:
            # 不消费令牌：避免他人持令牌即可使合法确认失效（拒绝服务）。
            raise PermissionDeniedError(
                "确认令牌不属于当前用户或会话",
                code="confirmation_forbidden",
                detail={"token": token[:8]},
            )
        await self._cache.delete(self._key(token))
        return pending
