"""WebSocket 推送：JWT 鉴权、频道订阅、按角色过滤与 ``broadcast``。

装配方式（**不修改 ``main.py``**）::

    from app.api.ws import attach_ws
    app = create_app()
    attach_ws(app)          # 注册 WS /ws 路由

``main.py`` 需**新增一行** ``attach_ws(app)`` 才能在生产启用推送（本 Task 不改
``main.py``，见交付说明）。

鉴权约定：access token 经**查询参数** ``?token=<jwt>`` 传入（前端 WS 无法自定义
请求头，与旧客户端一致）。缺失/无效/用户停用时**拒绝握手**（关闭码 4401），
未认证连接不会进入任何频道。

频道：``sentiment`` / ``pool`` / ``advice`` / ``alert``。其中 ``alert`` 为
**仅管理员**频道，非 admin 连接既不能订阅也收不到。

协议（与旧客户端 ``quant-web/src/lib/ws.ts`` 的期望对齐）::

    <- {"type":"hello","message":...}                连接成功
    <- {"type":"heartbeat","ts":<ms>}                服务端心跳（每 30s）
    -> {"type":"ping"}                               客户端保活
    -> {"type":"subscribe","channels":[...]}          设置订阅集合（替换）
    -> {"type":"unsubscribe","channels":[...]}        取消订阅
    <- {"type":<channel>,"channel":<channel>,"data":{...},"ts":<ms>}   频道推送

``broadcast(channel, payload)`` 可脱离服务器单测（见 ``tests/test_ws.py``）。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from fastapi import Depends, FastAPI, Query, WebSocket, WebSocketDisconnect

from app.core.errors import AuthError
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.models.auth import User
from app.repositories import Repositories, get_repositories
from app.repositories.auth import UserRepository

__all__ = [
    "ADMIN_ONLY_CHANNELS",
    "ALL_CHANNELS",
    "ConnectionManager",
    "Subscription",
    "WsSink",
    "attach_ws",
    "broadcast",
    "get_manager",
    "reset_manager",
]

logger = logging.getLogger(__name__)

#: 全部频道（行情类频道由采集完成事件 / 策略阶段推送薄事件，前端失效重取）。
ALL_CHANNELS: tuple[str, ...] = (
    "sentiment",
    "pool",
    "advice",
    "newsflash",
    "themes",
    "alert",
)

#: 仅管理员频道（运维告警）。
ADMIN_ONLY_CHANNELS: frozenset[str] = frozenset({"alert"})

#: 服务端心跳间隔（秒）。
HEARTBEAT_SECONDS = 30

#: 未认证关闭码（应用自定义区间）。
WS_CLOSE_UNAUTHORIZED = 4401


@runtime_checkable
class WsSink(Protocol):
    """可发送 JSON 的连接抽象（真实 ``WebSocket`` 或测试替身）。"""

    async def send_json(self, data: Any) -> None:
        """发送一条 JSON 消息。"""
        ...


@dataclass(slots=True)
class Subscription:
    """一个连接的订阅状态。"""

    sink: WsSink
    is_admin: bool
    channels: set[str] = field(default_factory=set)


def allowed_channels(is_admin: bool) -> set[str]:
    """某角色可订阅的频道集合（非管理员剔除仅管理员频道）。"""
    allowed = set(ALL_CHANNELS)
    if not is_admin:
        allowed -= ADMIN_ONLY_CHANNELS
    return allowed


class ConnectionManager:
    """连接与频道订阅管理器（进程内；``broadcast`` 无需活跃服务器即可单测）。"""

    def __init__(self) -> None:
        self._subs: dict[str, Subscription] = {}

    # ------------------------------------------------------------ 生命周期

    def register(
        self,
        conn_id: str,
        sink: WsSink,
        *,
        is_admin: bool,
        channels: set[str] | None = None,
    ) -> Subscription:
        """登记连接；默认订阅该角色可用的全部频道。"""
        selected = allowed_channels(is_admin) if channels is None else channels
        sub = Subscription(
            sink=sink, is_admin=is_admin, channels=selected & allowed_channels(is_admin)
        )
        self._subs[conn_id] = sub
        return sub

    def unregister(self, conn_id: str) -> None:
        """移除连接。"""
        self._subs.pop(conn_id, None)

    def get(self, conn_id: str) -> Subscription | None:
        """取某连接的订阅状态。"""
        return self._subs.get(conn_id)

    def count(self) -> int:
        """当前连接数。"""
        return len(self._subs)

    # ------------------------------------------------------------ 订阅变更

    def channels_for(self, conn_id: str) -> set[str]:
        """某连接当前订阅的频道集合。"""
        sub = self._subs.get(conn_id)
        return set(sub.channels) if sub is not None else set()

    def subscribe(self, conn_id: str, channels: list[str]) -> set[str]:
        """**设置**订阅集合（替换为 角色可用 ∩ 请求集合）。"""
        sub = self._subs.get(conn_id)
        if sub is None:
            return set()
        requested = {str(item) for item in channels}
        sub.channels = requested & allowed_channels(sub.is_admin)
        return set(sub.channels)

    def unsubscribe(self, conn_id: str, channels: list[str]) -> set[str]:
        """从订阅集合中移除指定频道。"""
        sub = self._subs.get(conn_id)
        if sub is None:
            return set()
        sub.channels -= {str(item) for item in channels}
        return set(sub.channels)

    # ------------------------------------------------------------ 广播

    def subscribers(self, channel: str) -> list[Subscription]:
        """可接收该频道的订阅（含角色过滤）。"""
        if channel in ADMIN_ONLY_CHANNELS:
            return [
                sub for sub in self._subs.values() if channel in sub.channels and sub.is_admin
            ]
        return [sub for sub in self._subs.values() if channel in sub.channels]

    async def broadcast(self, channel: str, payload: Any) -> int:
        """向订阅该频道的连接推送，返回送达数（发送失败的连接被剔除）。"""
        message = {
            "type": channel,
            "channel": channel,
            "data": payload,
            "ts": int(time.time() * 1000),
        }
        delivered = 0
        for conn_id, sub in list(self._subs.items()):
            if channel in ADMIN_ONLY_CHANNELS and not sub.is_admin:
                continue
            if channel not in sub.channels:
                continue
            try:
                await sub.sink.send_json(message)
            except Exception:
                logger.warning("ws_send_failed", exc_info=True, extra={"channel": channel})
                self.unregister(conn_id)
                continue
            delivered += 1
        return delivered


_manager = ConnectionManager()


def get_manager() -> ConnectionManager:
    """返回进程内连接管理器单例。"""
    return _manager


def reset_manager() -> None:
    """清空连接管理器（测试隔离用）。"""
    _manager._subs.clear()


async def broadcast(channel: str, payload: Any) -> int:
    """向指定频道广播（供采集完成 / 建议落库调用）。"""
    return await _manager.broadcast(channel, payload)


# ============================================================ 端点


async def _authenticate(token: str | None, repos: Repositories) -> User | None:
    """校验查询参数中的 access token 并加载用户；失败返回 ``None``。"""
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
    except AuthError:
        return None
    user = await UserRepository(repos.session).get_by_username(str(payload["sub"]))
    if user is None or not user.enabled:
        return None
    return user


async def _heartbeat(websocket: WebSocket) -> None:
    """周期性发送心跳，直到连接断开。"""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await websocket.send_json({"type": "heartbeat", "ts": int(time.time() * 1000)})
    except asyncio.CancelledError:  # pragma: no cover - 正常关闭路径
        raise
    except Exception:  # pragma: no cover - 连接已断开
        return


async def _handle_message(
    websocket: WebSocket, manager: ConnectionManager, conn_id: str, message: Any
) -> None:
    """处理一条客户端消息（``ping`` / ``subscribe`` / ``unsubscribe``）。"""
    if not isinstance(message, dict):
        return
    kind = str(message.get("type", ""))
    if kind == "ping":
        await websocket.send_json({"type": "heartbeat", "ts": int(time.time() * 1000)})
        return
    if kind == "subscribe":
        channels = manager.subscribe(conn_id, list(message.get("channels") or []))
        await websocket.send_json({"type": "subscribed", "channels": sorted(channels)})
        return
    if kind == "unsubscribe":
        channels = manager.unsubscribe(conn_id, list(message.get("channels") or []))
        await websocket.send_json({"type": "unsubscribed", "channels": sorted(channels)})
        return


async def ws_endpoint(
    websocket: WebSocket,
    token: str | None = Query(default=None, description="access token（查询参数）"),
    repos: Repositories = Depends(get_repositories),
) -> None:
    """``WS /ws``：鉴权 → 订阅 → 心跳 → 频道推送。"""
    try:
        user = await _authenticate(token, repos)
    except Exception:
        logger.warning("ws_auth_failed", exc_info=True)
        user = None
    if user is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    await websocket.accept()
    manager = get_manager()
    conn_id = uuid.uuid4().hex
    sub = manager.register(conn_id, websocket, is_admin=(user.role == "admin"))
    await websocket.send_json(
        {
            "type": "hello",
            "message": f"connected as {user.username}",
            "channels": sorted(sub.channels),
        }
    )
    heartbeat = asyncio.create_task(_heartbeat(websocket))
    try:
        while True:
            message = await websocket.receive_json()
            await _handle_message(websocket, manager, conn_id, message)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("ws_connection_error", exc_info=True, extra={"user": user.username})
    finally:
        heartbeat.cancel()
        manager.unregister(conn_id)


def attach_ws(app: FastAPI) -> None:
    """把 ``WS /ws`` 挂到应用上（``main.py`` 需调用一次）。

    同时接入 :mod:`app.core.ws_bus`（worker 进程发布的跨进程事件经 Redis
    中转到本进程连接；无 Redis 时发布端直接走本地广播）。

    **启动时机**：Starlette 1.x 移除了 ``add_event_handler`` 且不再执行
    ``router.on_startup``（指定 ``lifespan=`` 后该列表是死代码），因此这里
    用**包装 lifespan** 的方式保证订阅任务在应用启动时真正跑起来——
    在原始 lifespan 外面套一层，进入时启动 ``subscribe_ws_bus`` 任务，
    退出时取消。
    """
    from contextlib import asynccontextmanager

    from app.core import ws_bus

    ws_bus.set_local_broadcaster(_manager.broadcast)
    app.add_api_websocket_route("/ws", ws_endpoint)

    if getattr(app.router.lifespan_context, "_ws_bus_wrapped", False):
        return  # 已包装（重复 attach 幂等）
    original = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan_with_ws_bus(router: FastAPI):
        import asyncio

        subscriber = asyncio.create_task(ws_bus.subscribe_ws_bus())
        logger.info("ws_bus_subscriber_started")
        try:
            async with original(router):
                yield
        finally:
            subscriber.cancel()

    _lifespan_with_ws_bus._ws_bus_wrapped = True  # type: ignore[attr-defined]
    app.router.lifespan_context = _lifespan_with_ws_bus
