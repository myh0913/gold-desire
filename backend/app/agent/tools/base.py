"""Agent 工具层基类、注册表与执行上下文。

设计要点：

- :class:`AgentTool` 为工具协议：``name`` / ``description``（作为 LLM 工具描述）/
  ``parameters``（JSON Schema）/ ``mutating`` / ``required_role`` /
  ``requires_confirmation`` 与 ``async run(ctx, **kwargs)``。
- ``@register_tool`` 装饰器在导入期登记工具实例，:func:`all_tools` / :func:`get_tool`
  供权限过滤与会话循环使用；重复 ``name`` 在启动期直接报错。
- **参数用 Pydantic 校验**：非法参数抛 :class:`ToolArgumentError`，由会话循环转成
  结构化错误回给模型（不使会话崩溃）。
- :class:`ToolContext` 携带已认证用户、仓储容器、运行配置、会话 ID、缓存与审计钩子。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.cache import CacheBackend
from app.core.config import Settings
from app.models.auth import User
from app.repositories import Repositories

__all__ = [
    "AgentTool",
    "AuditHook",
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

#: 审计钩子签名：``(action, target, detail)``。
AuditHook = Callable[[str, str | None, "dict[str, Any] | None"], Awaitable[None]]


class ToolError(Exception):
    """工具执行错误（结构化返回给模型，不使会话崩溃）。"""

    code = "tool_error"

    def __init__(self, message: str, *, code: str | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.detail = detail

    def to_payload(self) -> dict[str, Any]:
        """转为回给模型的结构化错误载荷。"""
        return {"error": {"code": self.code, "message": self.message, "detail": self.detail}}


class ToolArgumentError(ToolError):
    """工具参数校验失败。"""

    code = "invalid_arguments"


class ToolRegistryError(RuntimeError):
    """工具注册表异常（缺少名称 / 重复名称 / 未注册）。"""


@dataclass(slots=True)
class ToolContext:
    """工具执行上下文。

    Attributes:
        user: 已认证用户（服务端权限的唯一依据）。
        repos: 仓储容器（工具经服务层/仓储访问数据，不直接触达数据源）。
        settings: 运行配置。
        session_id: 当前 Agent 会话 ID。
        cache: 缓存后端（HITL 待确认令牌存放于此）。
        audit: 审计钩子，写 ``AuditLog``。
    """

    user: User
    repos: Repositories
    settings: Settings
    session_id: str
    cache: CacheBackend
    audit: AuditHook

    @property
    def is_admin(self) -> bool:
        """当前用户是否为管理员。"""
        return self.user.role == "admin"


def build_tool_context(
    *,
    user: User,
    repos: Repositories,
    settings: Settings,
    session_id: str,
    cache: CacheBackend,
) -> ToolContext:
    """构造 :class:`ToolContext`，并绑定写 ``AuditLog`` 的审计钩子。"""

    async def _audit(action: str, target: str | None, detail: dict[str, Any] | None) -> None:
        await repos.audit_logs.record(
            actor=user.username, action=action, target=target, detail=detail
        )
        await repos.session.flush()

    return ToolContext(
        user=user, repos=repos, settings=settings, session_id=session_id, cache=cache, audit=_audit
    )


def tool_schema(model: type[BaseModel]) -> dict[str, Any]:
    """由 Pydantic 模型导出 OpenAI 工具参数 JSON Schema。"""
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema["additionalProperties"] = False
    return schema


class AgentTool(ABC):
    """工具协议基类。

    子类以类属性声明元数据，并实现 :meth:`run`。``mutating=True`` 的工具仅
    ``required_role`` 允许的角色（当前为 ``admin``）可用。
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    mutating: ClassVar[bool] = False
    required_role: ClassVar[str | None] = None
    requires_confirmation: ClassVar[bool] = False
    args_model: ClassVar[type[BaseModel] | None] = None

    def validate(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """用 ``args_model`` 校验参数，返回归一化后的 kwargs。

        Raises:
            ToolArgumentError: 参数不符合 schema。
        """
        model = type(self).args_model
        if model is None:
            return dict(arguments)
        try:
            parsed = model.model_validate(dict(arguments))
        except ValidationError as exc:
            raise ToolArgumentError(
                f"工具 {self.name} 参数校验失败",
                detail=exc.errors(include_url=False, include_input=False),
            ) from exc
        return parsed.model_dump()

    @abstractmethod
    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """执行工具并返回 JSON 可序列化结果。"""
        raise NotImplementedError

    async def execute(self, ctx: ToolContext, arguments: Mapping[str, Any]) -> Any:
        """校验参数后执行（供 HITL 确认端点等外部调用方使用）。"""
        return await self.run(ctx, **self.validate(arguments))

    def spec(self) -> dict[str, Any]:
        """OpenAI 工具声明。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def public_dict(self) -> dict[str, Any]:
        """面向客户端的工具元数据（按角色过滤后暴露）。"""
        return {
            "name": self.name,
            "description": self.description,
            "mutating": self.mutating,
            "requires_confirmation": self.requires_confirmation,
            "required_role": self.required_role,
        }


_REGISTRY: dict[str, AgentTool] = {}

ToolT = TypeVar("ToolT", bound=type[AgentTool])


def register_tool(cls: ToolT) -> ToolT:
    """注册工具类（装饰器用法），导入即生效。

    Raises:
        ToolRegistryError: 缺少 ``name``，或 ``name`` 已被另一工具占用。
    """
    if not cls.name:
        raise ToolRegistryError(f"{cls.__qualname__} 缺少 name")
    existing = _REGISTRY.get(cls.name)
    if existing is not None and type(existing) is not cls:
        raise ToolRegistryError(
            f"重复的工具名 {cls.name!r}：{type(existing).__qualname__} 与 {cls.__qualname__}"
        )
    _REGISTRY.setdefault(cls.name, cls())
    return cls


def all_tools() -> list[AgentTool]:
    """全部已注册工具（按名称升序）。"""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def get_tool(name: str) -> AgentTool:
    """按名称取工具。

    Raises:
        ToolRegistryError: 未注册。
    """
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise ToolRegistryError(f"未注册的工具：{name!r}") from exc


def to_openai_tools(tools: Sequence[AgentTool]) -> list[dict[str, Any]]:
    """批量转为 OpenAI 工具声明。"""
    return [tool.spec() for tool in tools]
