"""Agent 测试替身与解析辅助（**零网络**）。

- :class:`ScriptedClient`：按剧本逐轮产出增量的假模型客户端，满足
  :class:`~app.agent.client.LLMClient` 协议；
- :func:`text_delta` / :func:`tool_delta` / :func:`stop_delta`：构造流式增量；
- :func:`parse_sse`：把 SSE 响应体解析为事件列表。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from app.agent.client import AgentError, ChatMessage, ChatResult, StreamDelta, ToolCall


class ScriptedClient:
    """脚本化假模型客户端。

    Args:
        turns: 每一轮的增量列表，按调用顺序消费。
        loop_last: 剧本耗尽后是否反复重放最后一轮（用于预算/死循环测试）。
        error: 若给出，则第 ``error_after`` 次调用抛出该异常（用于模型失败测试）。
        error_after: 第几次调用抛错（从 1 起）。
    """

    def __init__(
        self,
        turns: Sequence[Sequence[StreamDelta]],
        *,
        loop_last: bool = False,
        error: AgentError | None = None,
        error_after: int = 1,
    ) -> None:
        self._turns = [list(turn) for turn in turns]
        self._loop_last = loop_last
        self._error = error
        self._error_after = error_after
        self.calls: list[list[ChatMessage]] = []
        self.tools_seen: list[list[dict[str, Any]]] = []

    def _next(self) -> list[StreamDelta]:
        """取下一轮增量。"""
        if self._loop_last:
            return self._turns[-1] if self._turns else []
        if self._turns:
            return self._turns.pop(0)
        return []

    def _record(
        self, messages: Sequence[ChatMessage], tools: Sequence[Mapping[str, Any]]
    ) -> None:
        """记录调用并决定是否抛错。"""
        self.calls.append(list(messages))
        self.tools_seen.append([dict(tool) for tool in tools])
        if self._error is not None and len(self.calls) == self._error_after:
            raise self._error

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Mapping[str, Any]],
        *,
        stream: bool = False,
    ) -> ChatResult:
        """非流式：把剧本聚合为一个完整回复。"""
        self._record(messages, tools)
        result = ChatResult()
        for delta in self._next():
            result.content += delta.text
            result.tool_calls.extend(delta.tool_calls)
            if delta.finish_reason is not None:
                result.finish_reason = delta.finish_reason
        return result

    async def chat_stream(
        self, messages: Sequence[ChatMessage], tools: Sequence[Mapping[str, Any]]
    ) -> AsyncIterator[StreamDelta]:
        """流式：逐条产出剧本增量。"""
        self._record(messages, tools)
        for delta in self._next():
            yield delta


def text_delta(text: str) -> StreamDelta:
    """文本增量。"""
    return StreamDelta(text=text)


def tool_delta(
    name: str, arguments: Mapping[str, Any] | None = None, *, call_id: str = "call-1"
) -> StreamDelta:
    """工具调用增量。"""
    return StreamDelta(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=dict(arguments or {}))],
        finish_reason="tool_calls",
    )


def stop_delta() -> StreamDelta:
    """结束增量。"""
    return StreamDelta(finish_reason="stop")


def parse_sse(body: str) -> list[dict[str, Any]]:
    """把 SSE 响应体解析为 ``[{"type": ..., "data": ...}]``。"""
    events: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if payload:
            events.append(json.loads(payload))
    return events


def event_types(events: Sequence[Mapping[str, Any]]) -> list[str]:
    """提取事件类型序列。"""
    return [str(event["type"]) for event in events]
