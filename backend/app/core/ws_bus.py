"""跨进程 WS 事件总线（Redis pub/sub，进程内兜底）。

为什么需要：WS 连接与 :class:`~app.api.ws.ConnectionManager` 都活在 **api 进程**，
而策略阶段 / 采集在 **worker 进程** 执行——worker 直接调用 ``broadcast`` 无人收到。
本模块用 Redis pub/sub 桥接：

- **发布端**（worker / api 任一进程）：:func:`publish_event` 把事件发到
  ``ws:<channel>``；无 Redis（内存缓存 / 单进程部署）时回退到进程内本地广播。
- **订阅端**（仅 api 进程，见 :func:`app.api.ws.attach_ws` 的启动钩子）：
  ``psubscribe("ws:*")``，把消息中转到本进程的 ``ConnectionManager``。

分层约定：core 不得 import api，故本地广播器由 api 层启动时注入
（:func:`set_local_broadcaster`）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.cache import RedisCache, get_cache

__all__ = ["publish_event", "set_local_broadcaster", "subscribe_ws_bus"]

logger = logging.getLogger(__name__)

#: Redis 频道前缀（``ws:<channel>``）。
_CHANNEL_PREFIX = "ws:"

#: 本地广播器（api 进程注入；``ConnectionManager.broadcast(channel, payload)``）。
_local_broadcaster: Callable[[str, Any], Awaitable[int]] | None = None


def set_local_broadcaster(broadcaster: Callable[[str, Any], Awaitable[int]] | None) -> None:
    """注入 / 清除进程内本地广播器（api 层启动时调用）。"""
    global _local_broadcaster
    _local_broadcaster = broadcaster


def _redis_client() -> Any | None:
    """取 Redis 客户端（非 Redis 后端返回 ``None``）。"""
    cache = get_cache()
    if isinstance(cache, RedisCache):
        return cache._client
    return None


async def publish_event(channel: str, payload: Any) -> None:
    """向 ``channel`` 发布一条 WS 事件（Redis 优先，失败/无 Redis 回退本地）。

    发布失败只记日志——推送是尽力而为，绝不影响业务执行。
    """
    client = _redis_client()
    if client is None:
        if _local_broadcaster is not None:
            try:
                await _local_broadcaster(channel, payload)
            except Exception:  # pragma: no cover - 推送失败不影响业务
                logger.warning("ws_bus_local_broadcast_failed", extra={"channel": channel})
        return
    try:
        await client.publish(f"{_CHANNEL_PREFIX}{channel}", json.dumps(payload, default=str))
    except Exception:  # pragma: no cover - Redis 抖动时回退本地
        logger.warning("ws_bus_publish_failed", exc_info=True, extra={"channel": channel})
        if _local_broadcaster is not None:
            try:
                await _local_broadcaster(channel, payload)
            except Exception:  # pragma: no cover
                pass


async def subscribe_ws_bus() -> None:
    """常驻订阅 ``ws:*`` 并中转到本地 ``ConnectionManager``（api 进程启动钩子）。

    非 Redis 后端时是 no-op（单进程部署下 :func:`publish_event` 直接走本地广播）。
    订阅断开时自动重连（退避 1s，防 Redis 重启丢订阅）。
    """
    client = _redis_client()
    if client is None:
        return

    import asyncio

    while True:
        try:
            pubsub = client.pubsub()
            await pubsub.psubscribe(f"{_CHANNEL_PREFIX}*")
            async for message in pubsub.listen():
                if message.get("type") not in ("pmessage", "message"):
                    continue
                raw_channel = message.get("channel")
                channel = (
                    raw_channel.decode() if isinstance(raw_channel, bytes) else str(raw_channel)
                )
                if not channel.startswith(_CHANNEL_PREFIX):
                    continue
                body = message.get("data")
                try:
                    payload = json.loads(body) if isinstance(body, (str, bytes, bytearray)) else body
                except (TypeError, ValueError):
                    payload = body
                if _local_broadcaster is not None:
                    await _local_broadcaster(channel[len(_CHANNEL_PREFIX) :], payload)
        except asyncio.CancelledError:  # pragma: no cover - 应用关停
            raise
        except Exception:  # pragma: no cover - Redis 断连重试
            logger.warning("ws_bus_subscribe_broken_retry", exc_info=True)
            await asyncio.sleep(1)
