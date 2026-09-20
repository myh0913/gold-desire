"""内置 Agent 验收测试（非安全专项）：工具过滤、流式事件、预算、HITL、审计、模型失败。

全程**零网络**：模型客户端一律用 :class:`tests.agent_fakes.ScriptedClient` 或
``httpx.MockTransport`` 替换。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from app.agent.client import (
    AgentAuthError,
    AgentRateLimitedError,
    AgentTimeoutError,
    AgentUpstreamError,
    ChatMessage,
    OpenAICompatibleClient,
)
from app.agent.loop import AgentEvent, AgentLoop, Budget
from app.agent.permissions import ConfirmationStore, available_tools
from app.api.agent import get_llm_client
from app.core.cache import get_cache
from app.core.config import Settings, get_settings
from app.core.errors import PermissionDeniedError
from app.models.auth import User
from app.models.derived import AgentToolCall
from app.repositories import Repositories
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.agent_fakes import (
    ScriptedClient,
    event_types,
    parse_sse,
    stop_delta,
    text_delta,
    tool_delta,
)
from tests.conftest import auth_header


def _user(role: str, username: str = "tester") -> User:
    """构造一个未落库的用户对象（仅供角色判定）。"""
    return User(username=username, password_hash="x", role=role, enabled=True)


def _override_client(app: FastAPI, fake: Any) -> None:
    """把 Agent 的模型客户端依赖替换为假客户端。"""
    app.dependency_overrides[get_llm_client] = lambda: fake


async def _create_session(client: httpx.AsyncClient, token: str) -> str:
    """经 API 创建会话并返回 ``session_id``。"""
    response = await client.post(
        "/api/agent/sessions", json={"title": "测试会话"}, headers=auth_header(token)
    )
    assert response.status_code == 201, response.text
    return str(response.json()["session_id"])


async def _chat(
    client: httpx.AsyncClient, token: str, session_id: str, message: str, **extra: Any
) -> list[dict[str, Any]]:
    """发起一轮对话并返回解析后的事件列表。"""
    response = await client.post(
        f"/api/agent/sessions/{session_id}/chat",
        json={"message": message, **extra},
        headers=auth_header(token),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


# ============================================================ 1. 工具过滤


def test_non_admin_tool_list_has_no_mutating_tools() -> None:
    """非 admin 的 ``available_tools`` 不含任何变更工具。"""
    viewer_tools = available_tools(_user("viewer"))
    analyst_tools = available_tools(_user("analyst", username="a"))
    admin_tools = available_tools(_user("admin", username="adm"))

    for tools in (viewer_tools, analyst_tools):
        assert tools, "只读工具应对所有已认证角色可见"
        assert all(not tool.mutating for tool in tools)
        names = {tool.name for tool in tools}
        assert "update_strategy" not in names
        assert "delete_factor" not in names

    admin_names = {tool.name for tool in admin_tools}
    assert {"update_strategy", "delete_strategy", "run_backtest"} <= admin_names
    assert len(admin_tools) > len(viewer_tools)


async def test_tools_endpoint_filtered_by_role(
    client: httpx.AsyncClient, admin_token: str, viewer_token: str
) -> None:
    """``GET /api/agent/tools`` 按角色过滤，不向 viewer 泄露 admin 工具名。"""
    viewer_response = await client.get("/api/agent/tools", headers=auth_header(viewer_token))
    assert viewer_response.status_code == 200
    viewer_items = viewer_response.json()["items"]
    assert viewer_items
    assert all(item["mutating"] is False for item in viewer_items)
    viewer_names = {item["name"] for item in viewer_items}
    assert "update_strategy" not in viewer_names
    assert "delete_strategy" not in viewer_names
    assert "query_table" in viewer_names

    admin_response = await client.get("/api/agent/tools", headers=auth_header(admin_token))
    admin_names = {item["name"] for item in admin_response.json()["items"]}
    assert {"update_strategy", "delete_strategy", "run_backtest"} <= admin_names


async def test_skills_endpoint_lists_builtin_skills(
    client: httpx.AsyncClient, viewer_token: str
) -> None:
    """技能清单对任意已登录用户开放，且包含四个预置技能。"""
    response = await client.get("/api/agent/skills", headers=auth_header(viewer_token))
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["items"]}
    assert {
        "今日涨停结构分析",
        "策略参数对比",
        "某日无候选原因排查",
        "数据健康巡检",
    } <= names


# ============================================================ 5. 流式事件


async def test_streaming_event_order(
    client: httpx.AsyncClient,
    app: FastAPI,
    admin_token: str,
    market_seed: dict[str, Any],
) -> None:
    """文本 + 一次工具调用产出有序事件流。"""
    _override_client(
        app,
        ScriptedClient(
            [
                [text_delta("正在分析"), tool_delta("list_strategies")],
                [text_delta("结论如下"), stop_delta()],
            ]
        ),
    )
    session_id = await _create_session(client, admin_token)
    events = await _chat(client, admin_token, session_id, "有哪些策略")

    assert event_types(events) == [
        "token",
        "tool_call_start",
        "tool_call_result",
        "token",
        "done",
    ]
    assert events[0]["data"]["text"] == "正在分析"
    assert events[1]["data"]["tool"] == "list_strategies"
    assert events[2]["data"]["ok"] is True
    assert events[3]["data"]["text"] == "结论如下"
    assert events[4]["data"]["reason"] == "completed"


async def test_history_persisted(
    client: httpx.AsyncClient,
    app: FastAPI,
    admin_token: str,
    market_seed: dict[str, Any],
) -> None:
    """会话消息全量落库并可按序回读。"""
    _override_client(
        app, ScriptedClient([[text_delta("答复"), stop_delta()]])
    )
    session_id = await _create_session(client, admin_token)
    await _chat(client, admin_token, session_id, "你好")

    response = await client.get(
        f"/api/agent/sessions/{session_id}/messages", headers=auth_header(admin_token)
    )
    assert response.status_code == 200
    roles = [item["role"] for item in response.json()["items"]]
    assert roles == ["user", "assistant"]

    listed = await client.get("/api/agent/sessions", headers=auth_header(admin_token))
    assert session_id in {item["session_id"] for item in listed.json()["items"]}


# ============================================================ 4. 预算控制


async def _run_loop(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
    budget: Budget,
    turns: Sequence[Sequence[Any]],
) -> list[AgentEvent]:
    """在真实会话上跑一次循环并收集事件。"""
    user = _user("admin")
    async with session_factory() as session:
        repos = Repositories.build(session)
        await repos.agent_sessions.create(session_id, user.username)
        await session.commit()
        loop = AgentLoop(
            client=ScriptedClient(turns, loop_last=True),
            repos=repos,
            settings=get_settings(),
            cache=get_cache(),
            user=user,
            session_id=session_id,
            budget=budget,
        )
        return [event async for event in loop.run("无限循环")]


async def test_budget_tool_call_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """工具调用次数超限时优雅终止（不抛异常）。"""
    events = await _run_loop(
        session_factory,
        "sess-budget-tools",
        Budget(max_turns=5, max_tool_calls=2, max_tokens=10**9),
        [[tool_delta("list_strategies")]],
    )
    types = [event.type for event in events]
    assert types[-1] == "done"
    assert types.count("tool_call_result") == 2
    assert events[-1].data["reason"] == "budget_exhausted"
    assert "上限" in events[-1].data["message"]


async def test_budget_max_turns(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """轮数超限时优雅终止并给出明确提示。"""
    events = await _run_loop(
        session_factory,
        "sess-budget-turns",
        Budget(max_turns=2, max_tool_calls=99, max_tokens=10**9),
        [[tool_delta("list_strategies")]],
    )
    assert events[-1].type == "done"
    assert events[-1].data["reason"] == "budget_exhausted"
    assert "最大轮数" in events[-1].data["message"]


async def test_budget_token_limit(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """token 预算耗尽时优雅终止。"""
    events = await _run_loop(
        session_factory,
        "sess-budget-tokens",
        Budget(max_turns=9, max_tool_calls=99, max_tokens=1),
        [[text_delta("这是一段足够长的文本" * 5), tool_delta("list_strategies")]],
    )
    assert events[-1].type == "done"
    assert events[-1].data["reason"] == "budget_exhausted"
    assert "token" in events[-1].data["message"]


# ============================================================ 技能种子化


async def test_skill_seeds_system_prompt(
    client: httpx.AsyncClient, app: FastAPI, admin_token: str
) -> None:
    """对话可指定技能名，其提示词被注入系统消息。"""
    fake = ScriptedClient([[text_delta("好"), stop_delta()]])
    _override_client(app, fake)
    session_id = await _create_session(client, admin_token)
    await _chat(client, admin_token, session_id, "巡检一下", skill="数据健康巡检")

    system = fake.calls[0][0]
    assert system.role == "system"
    assert "数据运维巡检员" in (system.content or "")


async def test_unknown_skill_is_404(client: httpx.AsyncClient, admin_token: str) -> None:
    """未知技能名在流开始前即以 404 拒绝。"""
    session_id = await _create_session(client, admin_token)
    response = await client.post(
        f"/api/agent/sessions/{session_id}/chat",
        json={"message": "hi", "skill": "不存在的技能"},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 404


# ============================================================ 6. HITL


async def test_hitl_confirmation_flow(
    client: httpx.AsyncClient,
    app: FastAPI,
    admin_token: str,
    market_seed: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """需确认的变更工具首轮不执行；确认后执行；令牌一次性。"""
    _override_client(
        app,
        ScriptedClient(
            [
                [tool_delta("delete_strategy", {"strategy_id": "dragon"})],
                [text_delta("已提交，等待确认"), stop_delta()],
            ]
        ),
    )
    session_id = await _create_session(client, admin_token)
    events = await _chat(client, admin_token, session_id, "删除 dragon 策略")

    assert "confirmation_required" in event_types(events)
    async with session_factory() as session:
        assert await Repositories.build(session).strategy_defs.get("dragon") is not None

    token = next(
        event["data"]["token"] for event in events if event["type"] == "confirmation_required"
    )
    confirm = await client.post(
        f"/api/agent/sessions/{session_id}/confirm",
        json={"token": token},
        headers=auth_header(admin_token),
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["ok"] is True
    assert body["tool"] == "delete_strategy"

    async with session_factory() as session:
        assert await Repositories.build(session).strategy_defs.get("dragon") is None

    replay = await client.post(
        f"/api/agent/sessions/{session_id}/confirm",
        json={"token": token},
        headers=auth_header(admin_token),
    )
    assert replay.status_code == 403, "已消费的确认令牌不得重放"


async def test_confirmation_token_bound_to_user_and_session() -> None:
    """令牌绑定会话 + 用户，跨用户/跨会话不可复用。"""
    store = ConfirmationStore(get_cache())
    pending = await store.create(
        session_id="s1",
        user_id="alice",
        tool_name="delete_strategy",
        arguments={"strategy_id": "x"},
    )
    with pytest.raises(PermissionDeniedError):
        await store.take(pending.token, session_id="s1", user_id="bob")
    with pytest.raises(PermissionDeniedError):
        await store.take(pending.token, session_id="s2", user_id="alice")

    taken = await store.take(pending.token, session_id="s1", user_id="alice")
    assert taken.tool_name == "delete_strategy"
    assert taken.arguments == {"strategy_id": "x"}

    with pytest.raises(PermissionDeniedError):
        await store.take(pending.token, session_id="s1", user_id="alice")


# ============================================================ 7. 审计完整性


async def test_tool_call_audit_rows(
    client: httpx.AsyncClient,
    app: FastAPI,
    admin_token: str,
    market_seed: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """成功调用留下含入参/耗时/结果的审计行。"""
    _override_client(
        app,
        ScriptedClient([[tool_delta("list_strategies")], [text_delta("完成"), stop_delta()]]),
    )
    session_id = await _create_session(client, admin_token)
    await _chat(client, admin_token, session_id, "列策略")

    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(AgentToolCall).where(AgentToolCall.session_id == session_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.tool_name == "list_strategies"
    assert row.arguments == {}
    assert row.ok is True
    assert row.denied is False
    assert row.duration_ms is not None
    assert row.result_summary


# ============================================================ 8. 模型失败


async def test_model_failure_emits_error_then_done(
    client: httpx.AsyncClient, app: FastAPI, admin_token: str
) -> None:
    """模型超时 → ``error`` + ``done``，请求不挂起。"""
    _override_client(app, ScriptedClient([], error=AgentTimeoutError("模型调用超时")))
    session_id = await _create_session(client, admin_token)
    events = await _chat(client, admin_token, session_id, "在吗")

    assert event_types(events) == ["error", "done"]
    assert events[0]["data"]["code"] == "agent_timeout"
    assert events[1]["data"]["reason"] == "model_error"


def _client_with(handler: Any) -> OpenAICompatibleClient:
    """构造注入 ``MockTransport`` 的客户端（零网络）。"""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleClient(
        Settings(agent_model_api_key="test-key", agent_model_base_url="https://model.test/v1"),
        http_client=http,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AgentAuthError),
        (403, AgentAuthError),
        (429, AgentRateLimitedError),
        (500, AgentUpstreamError),
    ],
)
async def test_client_maps_provider_status(status: int, expected: type[Exception]) -> None:
    """提供方状态码映射为结构化 Agent 异常（不抛裸 httpx 异常）。"""
    client = _client_with(lambda request: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(expected):
        await client.chat([ChatMessage(role="user", content="hi")], [])
    await client.aclose()


async def test_client_maps_timeout() -> None:
    """超时映射为 :class:`AgentTimeoutError`。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    client = _client_with(handler)
    with pytest.raises(AgentTimeoutError):
        await client.chat([ChatMessage(role="user", content="hi")], [])
    await client.aclose()


async def test_client_parses_streamed_tool_calls() -> None:
    """SSE 增量解析：文本片段拼接 + 工具调用聚合。"""
    chunks = [
        'data: {"choices":[{"delta":{"content":"你好"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        '"function":{"name":"list_strategies","arguments":"{"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"}"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=("\n\n".join(chunks) + "\n\n").encode(),
            headers={"content-type": "text/event-stream"},
        )

    client = _client_with(handler)
    deltas = [
        delta
        async for delta in client.chat_stream([ChatMessage(role="user", content="hi")], [])
    ]
    await client.aclose()

    assert "".join(delta.text for delta in deltas) == "你好"
    calls = [call for delta in deltas for call in delta.tool_calls]
    assert len(calls) == 1
    assert calls[0].name == "list_strategies"
    assert calls[0].arguments == {}


# ============================================================ 9. 会话所有权


async def test_session_ownership_enforced(
    client: httpx.AsyncClient,
    app: FastAPI,
    admin_token: str,
    make_user: Any,
    login: Any,
) -> None:
    """非属主既不能读也不能写他人会话；不存在的会话 404。"""
    await make_user("other-admin", role="admin")
    other_token = await login("other-admin")
    session_id = await _create_session(client, admin_token)

    read = await client.get(
        f"/api/agent/sessions/{session_id}/messages", headers=auth_header(other_token)
    )
    assert read.status_code == 403

    _override_client(app, ScriptedClient([[text_delta("x"), stop_delta()]]))
    post = await client.post(
        f"/api/agent/sessions/{session_id}/chat",
        json={"message": "hi"},
        headers=auth_header(other_token),
    )
    assert post.status_code == 403

    missing = await client.get(
        "/api/agent/sessions/does-not-exist/messages", headers=auth_header(other_token)
    )
    assert missing.status_code == 404


async def test_agent_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    """未登录访问 Agent 端点一律 401。"""
    for method, path, payload in (
        ("GET", "/api/agent/tools", None),
        ("GET", "/api/agent/skills", None),
        ("GET", "/api/agent/sessions", None),
        ("POST", "/api/agent/sessions", {"title": "x"}),
    ):
        response = await client.request(method, path, json=payload)
        assert response.status_code == 401, f"{path} -> {response.status_code}"


def test_skill_seed_used_in_system_prompt() -> None:
    """技能名可种子化系统提示词（供会话循环使用）。"""
    from app.agent.skills import get_skill, list_skills

    skills = list_skills()
    assert len(skills) >= 4
    for skill in skills:
        assert skill.system_prompt
        assert skill.tools
        assert get_skill(skill.name) is skill


def test_unknown_skill_raises() -> None:
    """未知技能名报 404 级错误。"""
    from app.agent.skills import get_skill
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        get_skill("不存在的技能")
