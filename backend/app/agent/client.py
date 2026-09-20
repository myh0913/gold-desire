"""OpenAI 兼容模型客户端与结构化错误映射。

设计要点（spec「内置 Agent」）：

- **不绑定供应商**：只用 ``httpx`` 直连 ``{base_url}/chat/completions``，带
  ``Authorization: Bearer {api_key}``，因此 DeepSeek / Qwen / vLLM / OpenAI 等任意
  OpenAI 兼容服务均可接入，SHALL NOT 依赖任何厂商 SDK。
- **错误结构化**：超时 → :class:`AgentTimeoutError`、401/403 → :class:`AgentAuthError`、
  429 → :class:`AgentRateLimitedError`、其余 → :class:`AgentUpstreamError`；
  **绝不把裸 ``httpx`` 异常抛给调用方**。
- **可注入**：:class:`LLMClient` 为结构化协议，测试用脚本化假客户端替换，
  无需真实模型或 API Key（测试零网络）。
- **降级不阻塞**：模型不可用/超时只产生明确错误，客户端不持有全局锁、不做无界重试。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import Settings, get_settings

__all__ = [
    "AgentAuthError",
    "AgentError",
    "AgentRateLimitedError",
    "AgentTimeoutError",
    "AgentUpstreamError",
    "ChatMessage",
    "ChatResult",
    "LLMClient",
    "OpenAICompatibleClient",
    "StreamDelta",
    "ToolCall",
]


# ============================================================ 错误层次


class AgentError(Exception):
    """Agent 领域异常基类（模型侧失败一律经此层次暴露）。"""

    code: str = "agent_error"

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class AgentTimeoutError(AgentError):
    """模型调用超时。"""

    code = "agent_timeout"


class AgentAuthError(AgentError):
    """模型鉴权失败（401/403）。"""

    code = "agent_auth"


class AgentRateLimitedError(AgentError):
    """模型限流（429）。"""

    code = "agent_rate_limited"


class AgentUpstreamError(AgentError):
    """模型服务不可用或返回非预期响应。"""

    code = "agent_upstream"


# ============================================================ 数据结构


@dataclass(slots=True)
class ToolCall:
    """模型请求的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> dict[str, Any]:
        """序列化为 OpenAI ``tool_calls`` 元素。"""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False, default=str),
            },
        }

    def to_dict(self) -> dict[str, Any]:
        """序列化为可入库的字典。"""
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ToolCall:
        """从 :meth:`to_dict` 的输出还原。"""
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            arguments=dict(data.get("arguments") or {}),
        )


@dataclass(slots=True)
class ChatMessage:
    """一条会话消息（与 OpenAI Chat Completions 消息结构对齐）。"""

    role: str
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        """序列化为 OpenAI 消息字典。"""
        if self.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": self.tool_call_id,
                "content": self.content or "",
            }
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [call.to_openai() for call in self.tool_calls]
        return payload


@dataclass(slots=True)
class ChatResult:
    """一次非流式模型调用的结果。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(slots=True)
class StreamDelta:
    """流式增量：文本片段、**已聚合完整**的工具调用、结束原因与用量。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


# ============================================================ 协议


class LLMClient(Protocol):
    """模型客户端协议（可注入替身，测试零网络）。"""

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Mapping[str, Any]],
        *,
        stream: bool = False,
    ) -> ChatResult:
        """一次性获取完整回复（``stream=True`` 时内部聚合流式增量）。"""
        ...

    def chat_stream(
        self, messages: Sequence[ChatMessage], tools: Sequence[Mapping[str, Any]]
    ) -> AsyncIterator[StreamDelta]:
        """流式获取回复增量。"""
        ...


# ============================================================ 实现


def _error_for_status(status_code: int, body: str) -> AgentError:
    """把非 2xx 状态码映射为结构化 Agent 异常。"""
    snippet = body[:500]
    if status_code in (401, 403):
        return AgentAuthError("模型鉴权失败，请检查 API Key", detail={"status": status_code})
    if status_code == 429:
        return AgentRateLimitedError("模型限流，请稍后重试", detail={"status": status_code})
    return AgentUpstreamError(
        f"模型服务返回错误（HTTP {status_code}）",
        detail={"status": status_code, "body": snippet},
    )


class OpenAICompatibleClient:
    """基于 ``httpx`` 的 OpenAI 兼容客户端。

    Args:
        settings: 运行配置；缺省读全局单例（复用 ``agent_model_*`` / ``agent_timeout_seconds``）。
        http_client: 可注入的 ``httpx.AsyncClient``（测试用）。
        timeout: 显式超时秒数；缺省取 ``settings.agent_timeout_seconds``。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> None:
        resolved = settings or get_settings()
        self._base_url = resolved.agent_model_base_url.rstrip("/")
        self._api_key = resolved.agent_model_api_key
        self._model = resolved.agent_model_name
        self._timeout = float(timeout if timeout is not None else resolved.agent_timeout_seconds)
        self._http = http_client
        self._owns_http = http_client is None

    @property
    def model(self) -> str:
        """当前模型名。"""
        return self._model

    def _headers(self) -> dict[str, str]:
        """构造请求头（有 Key 时带 Bearer 鉴权）。"""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _client(self) -> httpx.AsyncClient:
        """惰性构造内部 ``httpx`` 客户端。"""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    def _payload(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Mapping[str, Any]],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        """构造请求体。"""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [message.to_openai() for message in messages],
            "stream": stream,
        }
        if tools:
            payload["tools"] = [dict(tool) for tool in tools]
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def aclose(self) -> None:
        """关闭内部持有的 HTTP 连接池（注入的客户端不关闭）。"""
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    # ---------------------------------------------------------- 非流式

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Mapping[str, Any]],
        *,
        stream: bool = False,
    ) -> ChatResult:
        """调用 ``/chat/completions``。

        Raises:
            AgentTimeoutError: 请求超时。
            AgentAuthError / AgentRateLimitedError / AgentUpstreamError: 上游错误。
        """
        if stream:
            result = ChatResult()
            async for delta in self.chat_stream(messages, tools):
                result.content += delta.text
                result.tool_calls.extend(delta.tool_calls)
                if delta.finish_reason is not None:
                    result.finish_reason = delta.finish_reason
                result.prompt_tokens += delta.prompt_tokens
                result.completion_tokens += delta.completion_tokens
            return result

        url = f"{self._base_url}/chat/completions"
        try:
            response = await self._client().post(
                url,
                headers=self._headers(),
                json=self._payload(messages, tools, stream=False),
            )
        except httpx.TimeoutException as exc:
            raise AgentTimeoutError(
                f"模型调用超时（>{self._timeout:g}s）", detail={"timeout": self._timeout}
            ) from exc
        except httpx.RequestError as exc:
            raise AgentUpstreamError(f"模型服务不可达：{type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise _error_for_status(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError as exc:
            raise AgentUpstreamError("模型返回非 JSON 响应") from exc
        return _parse_completion(data)

    # ---------------------------------------------------------- 流式

    async def chat_stream(
        self, messages: Sequence[ChatMessage], tools: Sequence[Mapping[str, Any]]
    ) -> AsyncIterator[StreamDelta]:
        """以 SSE 流式获取增量。

        工具调用参数在上游按片段下发，本方法在客户端内**按 index 聚合**为完整
        :class:`ToolCall` 后于流末一次性产出，故上层无需处理碎片。
        """
        url = f"{self._base_url}/chat/completions"
        partial: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        prompt_tokens = 0
        completion_tokens = 0
        try:
            async with self._client().stream(
                "POST",
                url,
                headers=self._headers(),
                json=self._payload(messages, tools, stream=True),
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "ignore")
                    raise _error_for_status(response.status_code, body)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[len("data:") :].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
                    for choice in event.get("choices") or []:
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if text:
                            yield StreamDelta(text=str(text))
                        for raw_call in delta.get("tool_calls") or []:
                            index = int(raw_call.get("index", 0) or 0)
                            slot = partial.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            if raw_call.get("id"):
                                slot["id"] = str(raw_call["id"])
                            function = raw_call.get("function") or {}
                            if function.get("name"):
                                slot["name"] = str(function["name"])
                            if function.get("arguments"):
                                slot["arguments"] += str(function["arguments"])
                        if choice.get("finish_reason"):
                            finish_reason = str(choice["finish_reason"])
        except httpx.TimeoutException as exc:
            raise AgentTimeoutError(
                f"模型调用超时（>{self._timeout:g}s）", detail={"timeout": self._timeout}
            ) from exc
        except httpx.RequestError as exc:
            raise AgentUpstreamError(f"模型服务不可达：{type(exc).__name__}") from exc

        calls = [_finalize_call(index, slot) for index, slot in sorted(partial.items())]
        yield StreamDelta(
            tool_calls=calls,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


def _decode_arguments(raw: Any) -> dict[str, Any]:
    """把工具调用参数原文解码为字典（非法 JSON 时以 ``_raw`` 保留原文）。"""
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {"_raw": raw}


def _finalize_call(index: int, slot: Mapping[str, str]) -> ToolCall:
    """把聚合后的工具调用片段转为 :class:`ToolCall`。"""
    return ToolCall(
        id=slot.get("id") or f"call_{index}",
        name=slot.get("name", ""),
        arguments=_decode_arguments(slot.get("arguments") or ""),
    )


def _parse_completion(data: Mapping[str, Any]) -> ChatResult:
    """解析非流式响应体。"""
    choices = data.get("choices") or []
    if not choices:
        raise AgentUpstreamError("模型响应缺少 choices")
    message = choices[0].get("message") or {}
    calls: list[ToolCall] = []
    for raw_call in message.get("tool_calls") or []:
        function = raw_call.get("function") or {}
        calls.append(
            ToolCall(
                id=str(raw_call.get("id", "")),
                name=str(function.get("name", "")),
                arguments=_decode_arguments(function.get("arguments")),
            )
        )
    usage = data.get("usage") or {}
    return ChatResult(
        content=str(message.get("content") or ""),
        tool_calls=calls,
        finish_reason=choices[0].get("finish_reason"),
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
    )
