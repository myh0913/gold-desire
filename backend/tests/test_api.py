"""读 API 验收测试：零上游、零文件系统、强制分页、鉴权与有效性分段。

核心验收（spec「用户请求只读库」「高并发下响应稳定」）：

- **零上游**：把全部 provider 的 ``fetch`` 打桩为抛错，逐个命中读接口仍 200 且零调用；
- **零文件系统**：读路径不得 ``open()`` / ``Path.glob``（打桩为抛错后仍 200）；
- **强制分页**：``page_size=10000`` 收敛到上限并回显实际生效值。
"""

from __future__ import annotations

import builtins
import pathlib
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from app.core.errors import UpstreamError
from app.datasources.registry import all_providers
from app.models.auth import AuditLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import auth_header

#: 全部读接口（path, query params）。任何一条都不得触达上游/文件系统。
READ_ENDPOINTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("/api/stocks", {"keyword": "测试"}),
    ("/api/stocks/600001", {}),
    ("/api/bars/daily", {"code": "600001", "start": "2026-01-01", "end": "2026-06-30"}),
    ("/api/bars/minute", {"code": "600001", "date": "2026-01-07"}),
    ("/api/pools", {"date": "2026-06-03"}),
    ("/api/pools/limit_up", {"date": "2026-06-03", "min_continue_days": 2}),
    ("/api/ladder", {"start": "2026-06-01", "end": "2026-06-30"}),
    ("/api/ladder/dates", {"limit": 10}),
    ("/api/sentiment", {"date": "2026-06-03"}),
    ("/api/sentiment/history", {"days": 10}),
    ("/api/themes", {"date": "2026-06-03"}),
    ("/api/themes/dates", {"limit": 10}),
    ("/api/themes/2026-06-03/AI/stocks", {}),
    ("/api/newsflash", {"limit": 10}),
    ("/api/monitor", {"date": "2026-06-03"}),
    ("/api/advice", {"date": "2026-06-03"}),
    ("/api/advice/latest", {"date": "2026-06-03"}),
    ("/api/advice/dates", {"limit": 10}),
    ("/api/backtest/runs", {"limit": 10}),
    ("/api/backtest/runs/run-test-0001", {}),
    ("/api/strategies", {}),
    ("/api/strategies/dragon", {}),
    ("/api/strategies/dragon/versions", {}),
    ("/api/strategies/dragon/versions/diff", {"from": 1, "to": 2}),
    ("/api/factors", {}),
    ("/api/factors/first_yin_amplitude", {}),
    (
        "/api/factors/first_yin_amplitude/effectiveness",
        {"start": "2026-01-01", "end": "2026-06-30"},
    ),
    ("/api/factors/first_yin_amplitude/versions", {}),
    ("/api/factors/first_yin_amplitude/versions/diff", {"from": 1, "to": 2}),
    ("/api/datasources", {}),
    ("/api/ingest/jobs", {"limit": 10}),
    ("/api/ingest/health", {}),
    ("/api/pages", {}),
)


def _spy_providers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把全部 provider 的 ``fetch`` 打桩为抛错并记录调用。"""
    calls: list[str] = []

    async def _boom(self: Any, capability: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(capability)
        raise UpstreamError("测试中禁用上游取数", detail={"capability": capability})

    for cls in all_providers():
        monkeypatch.setattr(cls, "fetch", _boom)
    return calls


async def test_zero_upstream_all_read_endpoints(
    client: httpx.AsyncClient,
    admin_token: str,
    market_seed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上游全部不可用时，每个读接口仍返回 200 且零上游调用。"""
    calls = _spy_providers(monkeypatch)
    headers = auth_header(admin_token)

    for path, params in READ_ENDPOINTS:
        response = await client.get(path, params=params, headers=headers)
        assert response.status_code == 200, f"{path} -> {response.status_code} {response.text}"

    assert calls == [], f"读路径触达了上游：{calls}"


async def test_no_filesystem_scan_on_read_path(
    client: httpx.AsyncClient,
    admin_token: str,
    market_seed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读路径不得打开文件或扫描目录（打桩为抛错后仍 200）。"""

    def _no_open(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"读路径不应 open()：{args[:1]}")

    def _no_glob(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("读路径不应扫描目录")

    monkeypatch.setattr(builtins, "open", _no_open)
    monkeypatch.setattr(pathlib.Path, "glob", _no_glob)
    monkeypatch.setattr(pathlib.Path, "rglob", _no_glob)
    monkeypatch.setattr(pathlib.Path, "iterdir", _no_glob)

    headers = auth_header(admin_token)
    for path, params in READ_ENDPOINTS:
        response = await client.get(path, params=params, headers=headers)
        assert response.status_code == 200, f"{path} -> {response.status_code} {response.text}"


async def test_pagination_page_size_clamped(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """``page_size=10000`` 收敛到上限，并回显实际生效值。"""
    response = await client.get(
        "/api/stocks", params={"page_size": 10000}, headers=auth_header(admin_token)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["page_size"] == 200
    assert len(body["items"]) <= 200
    assert body["page"] == 1


async def test_ladder_pagination_clamped(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """天梯列表同样强制有界。"""
    response = await client.get(
        "/api/ladder",
        params={"start": "2026-06-01", "end": "2026-06-30", "page_size": 5000},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200
    assert response.json()["page_size"] == 200


async def test_newsflash_limit_clamped(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """快讯 ``limit`` 同样受页大小上限约束。"""
    response = await client.get(
        "/api/newsflash", params={"limit": 9999}, headers=auth_header(admin_token)
    )
    assert response.status_code == 200
    assert response.json()["page_size"] == 200


async def test_unauthenticated_read_is_401(
    client: httpx.AsyncClient, market_seed: dict[str, Any]
) -> None:
    """未登录读接口返回 401 且不泄露业务数据。"""
    response = await client.get("/api/stocks")
    assert response.status_code == 401
    assert "items" not in response.text


async def test_viewer_admin_write_is_403_and_audited(
    client: httpx.AsyncClient,
    viewer_token: str,
    market_seed: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """viewer 命中 admin 写接口 → 403 且写入越权审计。"""
    response = await client.put(
        "/api/strategies/dragon/config",
        json={"params": {"base_position": 0.3}},
        headers=auth_header(viewer_token),
    )
    assert response.status_code == 403
    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "permission_denied")
                )
            )
            .scalars()
            .all()
        )
    assert rows, "越权访问必须写入审计日志"


async def test_pages_registry_includes_runtime_additions(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """``GET /api/pages`` 返回全部页面 key，含运行时新增。"""
    from app.core.pages import PageKey, register_page

    register_page("sandbox", "沙箱页")
    response = await client.get("/api/pages", headers=auth_header(admin_token))
    assert response.status_code == 200
    keys = {item["key"] for item in response.json()["items"]}
    assert PageKey.REVIEW.value in keys
    assert PageKey.QUANTCONFIG.value in keys
    assert "sandbox" in keys


async def test_effectiveness_returns_per_segment(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """因子有效性返回 A/B/C 分段（不是只报聚合）。"""
    response = await client.get(
        "/api/factors/first_yin_amplitude/effectiveness",
        params={"start": "2026-01-01", "end": "2026-06-30"},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sample_count"] == 3
    assert body["buckets"], "应有档位输出"
    for bucket in body["buckets"]:
        segments = bucket["segments"]
        assert set(segments) >= {"A", "B", "C"}, "必须输出 A/B/C 三段"
        assert sum(item["n"] for item in segments.values()) == bucket["n"]
    total = sum(bucket["n"] for bucket in body["buckets"])
    assert total == body["sample_count"]


async def test_market_responses_carry_stale_flag(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """行情响应携带 ``stale`` / ``data_date`` 新鲜度标记。"""
    response = await client.get(
        "/api/sentiment", params={"date": "2026-06-03"}, headers=auth_header(admin_token)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["stale"] is False
    assert body["data_date"] == "2026-06-03"


@pytest.fixture(autouse=True)
def _isolate_strategy_order() -> Iterator[None]:
    """用例期间把能力取数顺序固定为 ``fake``（零网络），结束后恢复真实顺序。"""
    from app.datasources.contracts import CAPABILITY_CONTRACTS
    from app.datasources.providers import install_real_capability_order
    from app.datasources.registry import reset_capability_order, set_capability_order

    reset_capability_order()
    for capability in CAPABILITY_CONTRACTS:
        set_capability_order(capability, ["fake"])
    yield
    reset_capability_order()
    install_real_capability_order()


async def test_pages_available_to_any_authenticated_user(
    client: httpx.AsyncClient, viewer_token: str, market_seed: dict[str, Any]
) -> None:
    """页面注册表对任意已登录用户开放（前端导航/权限矩阵的数据源）。"""
    response = await client.get("/api/pages", headers=auth_header(viewer_token))
    assert response.status_code == 200
    assert response.json()["items"]


async def test_admin_write_creates_audit_row(
    client: httpx.AsyncClient,
    admin_token: str,
    market_seed: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """admin 写接口成功后写入审计日志。"""
    response = await client.put(
        "/api/strategies/dragon/config",
        json={"params": {"base_position": 0.33}, "note": "audit"},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"

    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "strategy_config_save")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].actor == "seed-admin"


async def test_datasource_prefs_and_ping(
    client: httpx.AsyncClient,
    admin_token: str,
    market_seed: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """数据源主备调整与连通性探测（admin）均成功并写审计。"""
    headers = auth_header(admin_token)
    prefs = await client.put(
        "/api/datasources/prefs",
        json={"prefs": {"daily_bars": ["fake"]}},
        headers=headers,
    )
    assert prefs.status_code == 200, prefs.text
    assert prefs.json()["prefs"] == {"daily_bars": ["fake"]}

    ping = await client.post(
        "/api/datasources/ping", json={"sources": ["fake"]}, headers=headers
    )
    assert ping.status_code == 200, ping.text
    assert ping.json()["results"]["fake"]["daily_bars"]["ok"] is True

    async with session_factory() as session:
        actions = {
            row.action
            for row in (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action.in_(["datasource_prefs_update", "datasource_ping"])
                    )
                )
            )
            .scalars()
            .all()
        }
    assert actions == {"datasource_prefs_update", "datasource_ping"}


async def test_datasource_enable_disable(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """数据源启停（admin）。"""
    headers = auth_header(admin_token)
    disabled = await client.post("/api/datasources/fake/disable", headers=headers)
    assert disabled.status_code == 200
    assert disabled.json() == {"source_id": "fake", "enabled": False}
    enabled = await client.post("/api/datasources/fake/enable", headers=headers)
    assert enabled.status_code == 200
    assert enabled.json() == {"source_id": "fake", "enabled": True}


async def test_strategy_rollback_and_enable_disable(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """策略版本回滚与启停（admin，写审计）。"""
    headers = auth_header(admin_token)
    rollback = await client.post("/api/strategies/dragon/rollback/1", headers=headers)
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["version"] == 3

    disabled = await client.post("/api/strategies/dragon/disable", headers=headers)
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    enabled = await client.post("/api/strategies/dragon/enable", headers=headers)
    assert enabled.json()["enabled"] is True


async def test_factor_config_save_and_versions(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """因子参数保存、版本列表与 diff。"""
    headers = auth_header(admin_token)
    saved = await client.put(
        "/api/factors/first_yin_amplitude/config",
        json={"params": {"min_amplitude": 0.09}},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 3

    versions = await client.get(
        "/api/factors/first_yin_amplitude/versions", headers=headers
    )
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()["items"]] == [3, 2, 1]

    diff = await client.get(
        "/api/factors/first_yin_amplitude/versions/diff",
        params={"from": 1, "to": 2},
        headers=headers,
    )
    assert diff.status_code == 200
    assert diff.json()["changed"]["min_amplitude"] == [0.08, 0.07]


async def test_ingest_jobs_and_trigger(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """采集任务列表、健康度与手动触发（admin）。"""
    headers = auth_header(admin_token)
    jobs = await client.get("/api/ingest/jobs", headers=headers)
    assert jobs.status_code == 200
    assert jobs.json()["items"]

    health = await client.get("/api/ingest/health", headers=headers)
    assert health.status_code == 200
    assert "daily_bars" in health.json()["capabilities"]

    trigger = await client.post(
        "/api/ingest/trigger",
        json={"task": "daily_bars", "trade_date": "2026-06-03"},
        headers=headers,
    )
    assert trigger.status_code == 200, trigger.text
    assert trigger.json()["task"] == "daily_bars"


async def test_backtest_run_endpoint(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """触发回测（同步执行）返回 run 记录并落库。"""
    response = await client.post(
        "/api/backtest/run",
        json={"start": "2026-01-01", "end": "2026-06-30", "strategy_id": "dragon"},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"]
    assert body["status"] in {"succeeded", "failed"}

    listed = await client.get("/api/backtest/runs", headers=auth_header(admin_token))
    assert listed.status_code == 200
    assert any(item["run_id"] == body["run_id"] for item in listed.json()["items"])
