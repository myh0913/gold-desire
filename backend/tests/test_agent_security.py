"""Agent 安全专项测试：服务端二次校验、审计留痕与 ``query_table`` 白名单/拒裸 SQL。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.agent.permissions import authorize
from app.agent.tools import TABLE_WHITELIST, ToolError, build_tool_context, get_tool
from app.api.agent import get_llm_client
from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.errors import PermissionDeniedError
from app.models.auth import AuditLog, User
from app.models.derived import AgentToolCall
from app.repositories import Repositories
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.agent_fakes import ScriptedClient, parse_sse, stop_delta, text_delta, tool_delta
from tests.conftest import auth_header


def _user(role: str, username: str = "tester") -> User:
    """构造未落库用户（仅供角色判定）。"""
    return User(username=username, password_hash="x", role=role, enabled=True)


async def _create_session(client: httpx.AsyncClient, token: str) -> str:
    """经 API 创建会话。"""
    response = await client.post(
        "/api/agent/sessions", json={"title": "安全测试"}, headers=auth_header(token)
    )
    assert response.status_code == 201, response.text
    return str(response.json()["session_id"])


async def _audit_rows(session: AsyncSession, action: str) -> list[AuditLog]:
    """按动作取审计行。"""
    result = await session.execute(select(AuditLog).where(AuditLog.action == action))
    return list(result.scalars().all())


# ============================================================ 2. 执行前二次校验


async def test_authorize_denies_crafted_tool_call_and_audits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """viewer 直接调用 admin 工具 → ``PermissionDeniedError``（403）且写审计。"""
    viewer = _user("viewer", username="crafted-viewer")
    async with session_factory() as session:
        repos = Repositories.build(session)
        tool = get_tool("update_strategy")
        with pytest.raises(PermissionDeniedError) as excinfo:
            await authorize(tool, viewer, repos=repos, session_id="sess-crafted")
        assert excinfo.value.status_code == 403
        rows = await _audit_rows(session, "agent_tool_denied")

    assert len(rows) == 1
    assert rows[0].actor == "crafted-viewer"
    assert rows[0].target == "update_strategy"
    assert rows[0].detail is not None
    assert rows[0].detail["role"] == "viewer"


async def test_authorize_allows_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """admin 调用变更工具通过二次校验且不产生拒绝审计。"""
    admin = _user("admin", username="ok-admin")
    async with session_factory() as session:
        repos = Repositories.build(session)
        await authorize(get_tool("update_strategy"), admin, repos=repos, session_id="sess-ok")
        rows = await _audit_rows(session, "agent_tool_denied")
    assert rows == []


async def test_viewer_crafted_tool_call_rejected_in_stream_and_audited(
    client: httpx.AsyncClient,
    app: FastAPI,
    viewer_token: str,
    market_seed: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """viewer 通过对话构造变更工具调用：被拒、写审计、工具从未执行。"""
    fake = ScriptedClient(
        [
            [tool_delta("update_strategy", {"strategy_id": "dragon", "label": "hacked"})],
            [text_delta("已收到"), stop_delta()],
        ]
    )
    app.dependency_overrides[get_llm_client] = lambda: fake
    session_id = await _create_session(client, viewer_token)

    response = await client.post(
        f"/api/agent/sessions/{session_id}/chat",
        json={"message": "把 dragon 策略改名"},
        headers=auth_header(viewer_token),
    )
    assert response.status_code == 200
    events = parse_sse(response.text)

    result_events = [event for event in events if event["type"] == "tool_call_result"]
    assert result_events and result_events[0]["data"]["denied"] is True
    assert any(
        event["type"] == "error" and event["data"]["code"] == "permission_denied"
        for event in events
    )

    # 模型清单里根本没有该工具（隐藏只是体验，拒绝才是边界）
    for tools in fake.tools_seen:
        assert "update_strategy" not in {item["function"]["name"] for item in tools}

    async with session_factory() as session:
        denied_audit = await _audit_rows(session, "agent_tool_denied")
        executed = await _audit_rows(session, "agent_update_strategy")
        calls = list(
            (
                await session.execute(
                    select(AgentToolCall).where(AgentToolCall.session_id == session_id)
                )
            )
            .scalars()
            .all()
        )

    assert denied_audit and denied_audit[0].target == "update_strategy"
    assert executed == [], "变更工具绝不能被 viewer 执行"
    assert len(calls) == 1
    assert calls[0].denied is True
    assert calls[0].ok is False
    assert calls[0].tool_name == "update_strategy"


async def test_confirm_endpoint_requires_session_owner(
    client: httpx.AsyncClient, admin_token: str, make_user: Any, login: Any
) -> None:
    """非属主无法调用确认端点。"""
    await make_user("confirm-other", role="admin")
    other_token = await login("confirm-other")
    session_id = await _create_session(client, admin_token)

    response = await client.post(
        f"/api/agent/sessions/{session_id}/confirm",
        json={"token": "whatever"},
        headers=auth_header(other_token),
    )
    assert response.status_code == 403


# ============================================================ 3. query_table


def _ctx(repos: Repositories) -> Any:
    """构造 admin 工具上下文。"""
    return build_tool_context(
        user=_user("admin", username="qt-admin"),
        repos=repos,
        settings=get_settings(),
        session_id="sess-qt",
        cache=get_cache(),
    )


async def test_query_table_rejects_raw_sql(
    session_factory: async_sessionmaker[AsyncSession],
    market_seed: dict[str, Any],
) -> None:
    """裸 SQL 字符串（分号/注释/关键字）一律拒绝。"""
    tool = get_tool("query_table")
    async with session_factory() as session:
        ctx = _ctx(Repositories.build(session))
        for payload in ("SELECT * FROM users", "1; DROP TABLE", "users; --"):
            with pytest.raises(ToolError) as excinfo:
                await tool.execute(
                    ctx,
                    {
                        "table": "stocks",
                        "filters": [{"field": "name", "op": "eq", "value": payload}],
                    },
                )
            assert excinfo.value.code == "raw_sql_rejected"

        for payload in ("SELECT * FROM users", "users; --", "daily_bars; DROP TABLE x"):
            with pytest.raises(ToolError) as excinfo:
                await tool.execute(ctx, {"table": payload})
            assert excinfo.value.code == "raw_sql_rejected"


async def test_query_table_rejects_non_whitelisted_table(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """非白名单表（含用户/审计等敏感表）被拒绝。"""
    tool = get_tool("query_table")
    async with session_factory() as session:
        ctx = _ctx(Repositories.build(session))
        for table in ("users", "roles", "audit_logs", "invitations", "raw_responses"):
            assert table not in TABLE_WHITELIST
            with pytest.raises(ToolError) as excinfo:
                await tool.execute(ctx, {"table": table})
            assert excinfo.value.code == "table_not_allowed"


async def test_query_table_rejects_unknown_field_and_extra_args(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """字段必须真实存在；多余字段（如 ``sql``）被 Pydantic 拒绝。"""
    tool = get_tool("query_table")
    async with session_factory() as session:
        ctx = _ctx(Repositories.build(session))
        with pytest.raises(ToolError) as excinfo:
            await tool.execute(
                ctx,
                {
                    "table": "daily_bars",
                    "filters": [{"field": "password_hash", "op": "eq", "value": "x"}],
                },
            )
        assert excinfo.value.code == "unknown_field"

        with pytest.raises(ToolError) as excinfo:
            await tool.execute(ctx, {"table": "daily_bars", "sql": "SELECT 1"})
        assert excinfo.value.code == "invalid_arguments"


async def test_query_table_accepts_whitelisted_structured_query(
    session_factory: async_sessionmaker[AsyncSession],
    market_seed: dict[str, Any],
) -> None:
    """白名单表 + 结构化过滤条件正常工作。"""
    tool = get_tool("query_table")
    async with session_factory() as session:
        ctx = _ctx(Repositories.build(session))
        result = await tool.execute(
            ctx,
            {
                "table": "daily_bars",
                "filters": [{"field": "code", "op": "eq", "value": "600001"}],
                "order_by": "trade_date",
                "order": "asc",
                "limit": 5,
            },
        )
    assert result["table"] == "daily_bars"
    assert result["count"] > 0
    assert result["count"] <= 5
    assert all(row["code"] == "600001" for row in result["items"])
