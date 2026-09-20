"""健康与就绪探针测试。

无需 PostgreSQL / Redis 即可运行：依赖默认 memory 缓存与 SQLite。
"""

from __future__ import annotations

import httpx
import pytest
from app.main import create_app


@pytest.fixture(scope="module")
async def client() -> httpx.AsyncClient:
    """构建基于 ASGI 的异步测试客户端。"""
    app = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac


async def test_health_liveness(client: httpx.AsyncClient) -> None:
    """``/health`` 应始终返回 200。"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_ready_checks(client: httpx.AsyncClient) -> None:
    """``/ready`` 应返回含 checks 的 JSON，且默认环境应就绪。"""
    resp = await client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert "checks" in body
    assert "database" in body["checks"]
    assert "cache" in body["checks"]
    assert body["checks"]["cache"]["backend"] == "memory"


async def test_api_router_mounted(client: httpx.AsyncClient) -> None:
    """``/api/health`` 已由 api_router 托管。"""
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_security_headers_present(client: httpx.AsyncClient) -> None:
    """响应应携带安全响应头。"""
    resp = await client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Strict-Transport-Security") is not None


async def test_request_id_header(client: httpx.AsyncClient) -> None:
    """每个响应都应返回 request_id 头。"""
    resp = await client.get("/health")
    assert resp.headers.get("X-Request-Id")
