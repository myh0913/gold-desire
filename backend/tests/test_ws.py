"""WebSocket 测试：鉴权拒绝、频道订阅过滤、按角色屏蔽仅管理员频道、广播可达性。

用**手写 ASGI WebSocket 驱动**在测试事件循环内直连应用（无需真实服务器/网络），
并对 :class:`~app.api.ws.ConnectionManager` 做纯单元测试。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.api.ws import (
    ADMIN_ONLY_CHANNELS,
    ConnectionManager,
    attach_ws,
    broadcast,
    get_manager,
    reset_manager,
)
from fastapi import FastAPI

# ============================================================ 测试替身


class FakeSink:
    """记录收到的 JSON 消息的假连接。"""

    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def send_json(self, data: Any) -> None:
        """记录一条消息。"""
        self.messages.append(data)


class WsClient:
    """最小 ASGI WebSocket 驱动（同一事件循环内驱动应用）。"""

    def __init__(self, app: FastAPI, query: str = "") -> None:
        self._app = app
        self._scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "wss",
            "path": "/ws",
            "raw_path": b"/ws",
            "root_path": "",
            "query_string": query.encode(),
            "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 443),
            "subprotocols": [],
            "state": {},
        }
        self._inbox: asyncio.Queue[Any] = asyncio.Queue()
        self._outbox: asyncio.Queue[Any] = asyncio.Queue()
        self._task: asyncio.Task[Any] | None = None

    async def connect(self) -> None:
        """发送 connect 并启动应用任务。"""
        await self._inbox.put({"type": "websocket.connect"})
        self._task = asyncio.create_task(self._app(self._scope, self._receive, self._send))

    async def _receive(self) -> Any:
        return await self._inbox.get()

    async def _send(self, message: Any) -> None:
        await self._outbox.put(message)

    async def recv(self, timeout: float = 2.0) -> Any:
        """接收一条服务端消息（``websocket.send`` 的 JSON 载荷已解码）。"""
        message = await asyncio.wait_for(self._outbox.get(), timeout)
        if message.get("type") == "websocket.send":
            return json.loads(message["text"])
        return message

    async def recv_until(self, message_type: str, timeout: float = 2.0) -> Any:
        """持续接收直到出现指定 ``type`` 的消息。"""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            message = await self.recv(max(0.01, remaining))
            if message.get("type") == message_type:
                return message

    async def drain(self, timeout: float = 0.1) -> list[Any]:
        """在超时窗口内收集已到达的消息（用于断言「未收到」）。"""
        collected: list[Any] = []
        while True:
            try:
                collected.append(await self.recv(timeout))
            except TimeoutError:
                return collected

    async def send_json(self, data: Any) -> None:
        """发送一条 JSON 消息。"""
        await self._inbox.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def disconnect(self) -> None:
        """发送 disconnect 并等待应用任务结束。"""
        await self._inbox.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 2.0)
            except TimeoutError:  # pragma: no cover - 防御性
                self._task.cancel()


@pytest.fixture(autouse=True)
def _clean_manager() -> AsyncIterator[None]:
    """每个用例前后清空连接管理器。"""
    reset_manager()
    yield
    reset_manager()


# ============================================================ 单元测试


async def test_broadcast_reaches_subscribed_clients_only() -> None:
    """广播只送达订阅了该频道的连接。"""
    manager = ConnectionManager()
    sentiment = FakeSink()
    pool = FakeSink()
    manager.register("c1", sentiment, is_admin=False, channels={"sentiment"})
    manager.register("c2", pool, is_admin=False, channels={"pool"})

    delivered = await manager.broadcast("sentiment", {"temperature": 60})
    assert delivered == 1
    assert [message["type"] for message in sentiment.messages] == ["sentiment"]
    assert pool.messages == []


async def test_non_admin_cannot_subscribe_admin_only_channel() -> None:
    """非 admin 订阅仅管理员频道会被剔除。"""
    manager = ConnectionManager()
    manager.register("c1", FakeSink(), is_admin=False, channels=set())
    assert manager.subscribe("c1", ["alert", "sentiment"]) == {"sentiment"}


async def test_admin_only_channel_not_delivered_to_non_admin() -> None:
    """即便强行登记，非 admin 连接也收不到仅管理员频道。"""
    manager = ConnectionManager()
    viewer = FakeSink()
    admin = FakeSink()
    manager.register("viewer", viewer, is_admin=False, channels={"alert"})
    manager.register("admin", admin, is_admin=True, channels={"alert"})

    delivered = await manager.broadcast("alert", {"source": "ingest"})
    assert delivered == 1
    assert viewer.messages == []
    assert len(admin.messages) == 1


async def test_subscribe_replaces_channel_set() -> None:
    """``subscribe`` 为设置语义（替换订阅集合）。"""
    manager = ConnectionManager()
    manager.register("c1", FakeSink(), is_admin=False)
    assert manager.channels_for("c1") == {"sentiment", "pool", "advice"}
    assert manager.subscribe("c1", ["sentiment"]) == {"sentiment"}
    assert manager.unsubscribe("c1", ["sentiment"]) == set()


async def test_broadcast_drops_broken_connections() -> None:
    """发送失败的连接被剔除，不影响其他连接。"""

    class Broken:
        async def send_json(self, data: Any) -> None:
            raise RuntimeError("socket closed")

    manager = ConnectionManager()
    manager.register("broken", Broken(), is_admin=False, channels={"sentiment"})
    good = FakeSink()
    manager.register("good", good, is_admin=False, channels={"sentiment"})

    delivered = await manager.broadcast("sentiment", {"x": 1})
    assert delivered == 1
    assert manager.count() == 1
    assert len(good.messages) == 1


async def test_module_level_broadcast_uses_singleton() -> None:
    """模块级 ``broadcast`` 作用于进程内单例管理器。"""
    sink = FakeSink()
    get_manager().register("c1", sink, is_admin=True, channels={"advice"})
    delivered = await broadcast("advice", {"items": []})
    assert delivered == 1
    assert sink.messages[0]["channel"] == "advice"


# ============================================================ 端点集成


async def test_ws_unauthenticated_connect_rejected(app: FastAPI) -> None:
    """未携带 token 的连接被拒绝握手（关闭码 4401）。"""
    attach_ws(app)
    client = WsClient(app)
    await client.connect()
    message = await client.recv()
    assert message["type"] == "websocket.close"
    assert message["code"] == 4401


async def test_ws_invalid_token_rejected(app: FastAPI) -> None:
    """无效 token 同样被拒绝。"""
    attach_ws(app)
    client = WsClient(app, query="token=not-a-jwt")
    await client.connect()
    message = await client.recv()
    assert message["type"] == "websocket.close"
    assert message["code"] == 4401


async def test_ws_subscribe_filters_channels(app: FastAPI, admin_token: str) -> None:
    """已认证连接：订阅后只收到所订阅频道的推送。"""
    attach_ws(app)
    client = WsClient(app, query=f"token={admin_token}")
    await client.connect()
    hello = await client.recv_until("hello")
    assert "channels" in hello

    await client.send_json({"type": "subscribe", "channels": ["sentiment"]})
    subscribed = await client.recv_until("subscribed")
    assert subscribed["channels"] == ["sentiment"]

    await broadcast("pool", {"pool_type": "limit_up"})
    assert await client.drain(0.1) == [], "未订阅 pool 却收到了推送"

    await broadcast("sentiment", {"temperature": 61})
    message = await client.recv()
    assert message["type"] == "sentiment"
    assert message["data"] == {"temperature": 61}

    await client.disconnect()


async def test_ws_non_admin_blocked_from_admin_channel(
    app: FastAPI, viewer_token: str
) -> None:
    """非 admin 连接订阅 ``alert`` 得到空集合，且收不到 alert 推送。"""
    attach_ws(app)
    client = WsClient(app, query=f"token={viewer_token}")
    await client.connect()
    await client.recv_until("hello")

    await client.send_json({"type": "subscribe", "channels": [*ADMIN_ONLY_CHANNELS, "advice"]})
    subscribed = await client.recv_until("subscribed")
    assert subscribed["channels"] == ["advice"]

    await broadcast("alert", {"source": "ingest", "message": "boom"})
    assert await client.drain(0.1) == []

    await broadcast("advice", {"date": "2026-06-03"})
    message = await client.recv()
    assert message["type"] == "advice"

    await client.disconnect()
