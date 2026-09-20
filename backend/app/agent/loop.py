"""Agent 会话循环：多轮工具调用、预算控制、流式事件、上下文裁剪与全量审计。

流程：构造消息（系统提示词 + 技能 + 裁剪后的历史）→ 调用模型（流式）→ 若有工具调用则
**执行前二次鉴权** → 执行并追加结果 → 重复，直到模型给出最终答复或预算耗尽。

关键约束：

- **预算控制**：``agent_max_turns``（轮数）、``agent_max_tool_calls``（工具调用次数）与
  token 预算；任一超限即**优雅终止**并给出明确提示（不抛异常）。
- **流式事件**：产出 ``token`` / ``tool_call_start`` / ``tool_call_result`` /
  ``confirmation_required`` / ``error`` / ``done`` 六类事件，供 SSE 端点直接转发。
- **上下文裁剪**：按消息条数（:data:`MAX_CONTEXT_MESSAGES`）与估算 token
  （:data:`MAX_CONTEXT_TOKENS`）双上限，**优先保留最近消息**；跨轮重建时只保留
  user/assistant 的文本（不重放历史工具调用，避免出现孤立的 ``tool`` 消息导致上游 400）。
- **审计**：每条消息经 ``AgentMessageRepository`` 落库；每次工具调用经
  ``AgentToolCallRepository`` 记录入参、结果摘要、耗时、是否被拒。
- **模型失败**：捕获 :class:`~app.agent.client.AgentError`，发 ``error`` + ``done`` 后结束，
  绝不挂起流。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.agent.client import AgentError, ChatMessage, LLMClient, StreamDelta, ToolCall
from app.agent.permissions import ConfirmationStore, authorize, available_tools
from app.agent.skills import DEFAULT_SYSTEM_PROMPT, AgentSkill
from app.agent.tools import (
    AgentTool,
    ToolError,
    ToolRegistryError,
    build_tool_context,
    get_tool,
    to_openai_tools,
)
from app.core.cache import CacheBackend
from app.core.config import Settings
from app.core.errors import AppError, PermissionDeniedError
from app.models.auth import User
from app.models.derived import AgentMessage
from app.repositories import Repositories

__all__ = [
    "DEFAULT_MAX_TOKENS",
    "EVENT_CONFIRMATION",
    "EVENT_DONE",
    "EVENT_ERROR",
    "EVENT_TOKEN",
    "EVENT_TOOL_RESULT",
    "EVENT_TOOL_START",
    "MAX_CONTEXT_MESSAGES",
    "MAX_CONTEXT_TOKENS",
    "AgentEvent",
    "AgentLoop",
    "Budget",
]

logger = logging.getLogger(__name__)

#: 事件类型。
EVENT_TOKEN = "token"
EVENT_TOOL_START = "tool_call_start"
EVENT_TOOL_RESULT = "tool_call_result"
EVENT_CONFIRMATION = "confirmation_required"
EVENT_ERROR = "error"
EVENT_DONE = "done"

#: 单会话 token 预算缺省值（配置项 ``agent_max_tokens`` 未提供，故以常量兜底）。
DEFAULT_MAX_TOKENS = 60_000

#: 上下文裁剪上限：消息条数与估算 token。
MAX_CONTEXT_MESSAGES = 40
MAX_CONTEXT_TOKENS = 12_000

#: token 估算口径（约 4 字符 / token）。
_CHARS_PER_TOKEN = 4

#: 工具结果摘要长度上限。
_SUMMARY_LIMIT = 500


def _estimate(text: str | None) -> int:
    """按字符数估算 token 用量。"""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _ms(started: float) -> int:
    """毫秒耗时。"""
    return int((time.perf_counter() - started) * 1000)


def _summarize(payload: Any) -> str:
    """把任意结果压成定长摘要（供审计）。"""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return text if len(text) <= _SUMMARY_LIMIT else text[: _SUMMARY_LIMIT - 3] + "..."


@dataclass(slots=True)
class AgentEvent:
    """一条流式事件。"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """序列化为 SSE 帧。"""
        body = json.dumps({"type": self.type, "data": self.data}, ensure_ascii=False, default=str)
        return f"data: {body}\n\n"


@dataclass(slots=True)
class Budget:
    """单会话调用预算。"""

    max_turns: int = 12
    max_tool_calls: int = 30
    max_tokens: int = DEFAULT_MAX_TOKENS

    @classmethod
    def from_settings(cls, settings: Settings) -> Budget:
        """按配置构造（复用 ``agent_max_turns`` / ``agent_max_tool_calls``）。"""
        return cls(
            max_turns=max(1, settings.agent_max_turns),
            max_tool_calls=max(1, settings.agent_max_tool_calls),
            max_tokens=DEFAULT_MAX_TOKENS,
        )


class AgentLoop:
    """一次会话轮次的执行器（每个 chat 请求构造一个实例）。"""

    def __init__(
        self,
        *,
        client: LLMClient,
        repos: Repositories,
        settings: Settings,
        cache: CacheBackend,
        user: User,
        session_id: str,
        budget: Budget | None = None,
    ) -> None:
        self._client = client
        self._repos = repos
        self._settings = settings
        self._cache = cache
        self._user = user
        self._session_id = session_id
        self._budget = budget if budget is not None else Budget.from_settings(settings)
        self._confirmations = ConfirmationStore(cache)
        self._tokens = 0
        self._ctx = build_tool_context(
            user=user, repos=repos, settings=settings, session_id=session_id, cache=cache
        )

    # ------------------------------------------------------------------ 入口

    async def run(
        self, user_message: str, *, skill: AgentSkill | None = None
    ) -> AsyncIterator[AgentEvent]:
        """执行一轮用户提问，产出流式事件直到结束。"""
        history = await self._repos.agent_messages.list_session(self._session_id, limit=200)
        await self._repos.agent_messages.append(self._session_id, "user", user_message)
        messages = self._build_messages(history, user_message, skill)
        schemas = to_openai_tools(available_tools(self._user))

        turns = 0
        tool_calls_used = 0
        stop_message: str | None = None

        while turns < self._budget.max_turns:
            turns += 1
            text_parts: list[str] = []
            calls: list[ToolCall] = []
            try:
                async for delta in self._client.chat_stream(messages, schemas):
                    self._account(delta)
                    if delta.text:
                        text_parts.append(delta.text)
                        yield AgentEvent(EVENT_TOKEN, {"text": delta.text})
                    if delta.tool_calls:
                        calls.extend(delta.tool_calls)
            except AgentError as exc:
                logger.warning("agent_model_failed", extra={"code": exc.code, "detail": exc.detail})
                stop_message = f"模型调用失败：{exc.message}"
                await self._persist_assistant(stop_message, [])
                await self._commit()
                yield AgentEvent(EVENT_ERROR, {"code": exc.code, "message": exc.message})
                yield AgentEvent(EVENT_DONE, {"reason": "model_error", "message": stop_message})
                return

            assistant_text = "".join(text_parts)
            await self._persist_assistant(assistant_text, calls)
            messages.append(
                ChatMessage(
                    role="assistant", content=assistant_text or None, tool_calls=list(calls)
                )
            )

            if not calls:
                await self._commit()
                yield AgentEvent(EVENT_DONE, {"reason": "completed", "message": assistant_text})
                return

            for call in calls:
                if tool_calls_used >= self._budget.max_tool_calls:
                    stop_message = (
                        f"工具调用次数已达上限（{self._budget.max_tool_calls}），已终止本次会话"
                    )
                    break
                tool_calls_used += 1
                async for event in self._handle_call(call, messages):
                    yield event
            if stop_message is not None:
                break
            if self._tokens >= self._budget.max_tokens:
                stop_message = f"token 预算已耗尽（上限 {self._budget.max_tokens}），已终止本次会话"
                break

        if stop_message is None:
            stop_message = f"已达最大轮数（{self._budget.max_turns}），已终止本次会话"
        await self._persist_assistant(stop_message, [])
        await self._commit()
        yield AgentEvent(EVENT_DONE, {"reason": "budget_exhausted", "message": stop_message})

    # ------------------------------------------------------------------ 工具调用

    async def _handle_call(
        self, call: ToolCall, messages: list[ChatMessage]
    ) -> AsyncIterator[AgentEvent]:
        """处理一次工具调用：未知工具 / 二次鉴权 / 参数校验 / HITL / 执行。"""
        started = time.perf_counter()
        try:
            tool = get_tool(call.name)
        except ToolRegistryError:
            message = f"未知工具：{call.name}"
            await self._record_call(call, ok=False, duration_ms=_ms(started), summary=message)
            await self._append_tool_result(
                call, messages, {"error": {"code": "unknown_tool", "message": message}}
            )
            yield AgentEvent(
                EVENT_TOOL_RESULT, {"tool": call.name, "ok": False, "summary": message}
            )
            return

        yield AgentEvent(EVENT_TOOL_START, {"tool": tool.name, "arguments": call.arguments})

        try:
            await authorize(tool, self._user, repos=self._repos, session_id=self._session_id)
        except PermissionDeniedError as exc:
            await self._record_call(
                call, ok=False, denied=True, duration_ms=_ms(started), summary=exc.message
            )
            await self._append_tool_result(
                call, messages, {"error": {"code": "permission_denied", "message": exc.message}}
            )
            yield AgentEvent(
                EVENT_TOOL_RESULT,
                {"tool": tool.name, "ok": False, "denied": True, "summary": exc.message},
            )
            yield AgentEvent(
                EVENT_ERROR,
                {"code": "permission_denied", "message": exc.message, "tool": tool.name},
            )
            return

        try:
            arguments = tool.validate(call.arguments)
        except ToolError as exc:
            await self._record_call(
                call, ok=False, duration_ms=_ms(started), summary=exc.message
            )
            await self._append_tool_result(call, messages, exc.to_payload())
            yield AgentEvent(
                EVENT_TOOL_RESULT,
                {"tool": tool.name, "ok": False, "code": exc.code, "summary": exc.message},
            )
            return

        if tool.requires_confirmation:
            pending = await self._confirmations.create(
                session_id=self._session_id,
                user_id=self._user.username,
                tool_name=tool.name,
                arguments=arguments,
            )
            await self._record_call(
                call,
                ok=True,
                duration_ms=_ms(started),
                summary=f"pending_confirmation:{pending.token[:8]}",
            )
            await self._append_tool_result(
                call,
                messages,
                {
                    "status": "pending_confirmation",
                    "token": pending.token,
                    "message": f"{tool.name} 为危险操作，需人工确认后方可执行",
                },
            )
            yield AgentEvent(
                EVENT_CONFIRMATION,
                {
                    "token": pending.token,
                    "tool": tool.name,
                    "arguments": arguments,
                    "expires_at": pending.expires_at,
                },
            )
            yield AgentEvent(
                EVENT_TOOL_RESULT,
                {
                    "tool": tool.name,
                    "ok": True,
                    "pending_confirmation": True,
                    "summary": "等待确认",
                },
            )
            return

        payload, ok = await self._invoke(tool, arguments)
        summary = _summarize(payload)
        await self._record_call(call, ok=ok, duration_ms=_ms(started), summary=summary)
        await self._append_tool_result(call, messages, payload)
        yield AgentEvent(EVENT_TOOL_RESULT, {"tool": tool.name, "ok": ok, "summary": summary})

    async def _invoke(self, tool: AgentTool, arguments: dict[str, Any]) -> tuple[Any, bool]:
        """执行工具并把异常转为结构化载荷。"""
        try:
            result = await tool.run(self._ctx, **arguments)
        except ToolError as exc:
            return exc.to_payload(), False
        except AppError as exc:
            return {"error": {"code": exc.code, "message": exc.message}}, False
        except Exception as exc:  # 防御性：任何工具内部异常都不应中断会话
            logger.exception("agent_tool_failed", extra={"tool": tool.name})
            message = f"{type(exc).__name__}: {exc}"
            return {"error": {"code": "tool_failed", "message": message}}, False
        return {"ok": True, "result": result}, True

    # ------------------------------------------------------------------ 持久化

    async def _append_tool_result(
        self, call: ToolCall, messages: list[ChatMessage], payload: Any
    ) -> None:
        """把工具结果同时写入模型上下文与消息审计表。"""
        content = json.dumps(payload, ensure_ascii=False, default=str)
        messages.append(
            ChatMessage(role="tool", tool_call_id=call.id, name=call.name, content=content)
        )
        await self._repos.agent_messages.append(
            self._session_id, "tool", content, tokens=_estimate(content)
        )

    async def _persist_assistant(self, text: str, calls: Sequence[ToolCall]) -> None:
        """落库一条 assistant 消息（含其请求的工具调用）。"""
        await self._repos.agent_messages.append(
            self._session_id,
            "assistant",
            text or None,
            tool_calls=[call.to_dict() for call in calls] or None,
            tokens=_estimate(text),
        )

    async def _record_call(
        self,
        call: ToolCall,
        *,
        ok: bool,
        duration_ms: int,
        summary: str | None,
        denied: bool = False,
    ) -> None:
        """写一条工具调用审计。"""
        await self._repos.agent_tool_calls.record(
            self._session_id,
            call.name,
            call.arguments,
            ok=ok,
            denied=denied,
            duration_ms=duration_ms,
            result_summary=summary,
        )

    async def _commit(self) -> None:
        """提交当前事务（确保流式过程中产生的审计/消息已落库）。"""
        await self._repos.session.commit()

    # ------------------------------------------------------------------ 上下文

    def _account(self, delta: StreamDelta) -> None:
        """累计 token 用量（优先用上游 usage，否则按字符估算）。"""
        reported = delta.prompt_tokens + delta.completion_tokens
        self._tokens += reported if reported else _estimate(delta.text)

    def _build_messages(
        self,
        history: Sequence[AgentMessage],
        user_message: str,
        skill: AgentSkill | None,
    ) -> list[ChatMessage]:
        """构造发送给模型的消息列表。"""
        system = skill.system_prompt if skill is not None else DEFAULT_SYSTEM_PROMPT
        messages = [ChatMessage(role="system", content=system)]
        for row in self._trim_history(history):
            role = str(row.role)
            content = row.content or ""
            if not content or role not in {"user", "assistant"}:
                continue
            messages.append(ChatMessage(role=role, content=content))
        messages.append(ChatMessage(role="user", content=user_message))
        return messages

    @staticmethod
    def _trim_history(history: Sequence[AgentMessage]) -> list[AgentMessage]:
        """按条数与估算 token 双上限裁剪历史，优先保留最近消息。"""
        selected: list[AgentMessage] = []
        budget = MAX_CONTEXT_TOKENS
        for row in reversed(history):
            if len(selected) >= MAX_CONTEXT_MESSAGES:
                break
            cost = _estimate(row.content)
            if selected and cost > budget:
                break
            budget -= cost
            selected.append(row)
        selected.reverse()
        return selected
