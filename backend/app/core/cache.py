"""可插拔缓存层。

设计要点：

- ``CacheBackend`` 为结构化 Protocol，所有实现均为 **async**。
- ``MemoryCache`` 为默认后端：TTL 感知 + 最大条目数 LRU 淘汰，
  兼作读路径的进程内 L2 缓存，**不需要 Redis 即可启动**。
- ``RedisCache`` 延迟导入 ``redis``（模块导入期不 import redis），
  仅在 ``cache_backend=redis`` 时构造。
- ``get_cache()`` 返回进程内单例，由 ``close_cache()`` 释放。
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Protocol, runtime_checkable

from app.core.config import Settings, get_settings
from app.core.logging import get_request_id

MISSING = object()


@runtime_checkable
class CacheBackend(Protocol):
    """缓存后端协议。"""

    async def get(self, key: str) -> Any | None:
        """读取键值，不存在或已过期返回 ``None``。"""
        ...

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """写入键值，``ttl`` 为秒；``None`` 表示使用默认 TTL。"""
        ...

    async def delete(self, key: str) -> None:
        """删除单个键。"""
        ...

    async def delete_prefix(self, prefix: str) -> int:
        """按前缀批量删除，返回删除数量。"""
        ...

    async def close(self) -> None:
        """释放底层资源。"""
        ...


class MemoryCache:
    """进程内 TTL + LRU 缓存。

    Args:
        max_entries: 最大条目数，超出时淘汰最久未使用项。
        default_ttl: 未显式指定 ttl 时使用的秒数。
    """

    def __init__(self, max_entries: int = 10_000, default_ttl: int = 60) -> None:
        self._max_entries = max(1, max_entries)
        self._default_ttl = max(1, default_ttl)
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _is_expired(self, expires_at: float) -> bool:
        return expires_at != 0.0 and expires_at <= time.monotonic()

    async def get(self, key: str) -> Any | None:
        """读取键值，命中时刷新 LRU 顺序。"""
        async with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, value = item
            if self._is_expired(expires_at):
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """写入键值并执行 LRU 淘汰。"""
        effective_ttl = self._default_ttl if ttl is None else ttl
        expires_at = 0.0 if effective_ttl <= 0 else time.monotonic() + effective_ttl
        async with self._lock:
            self._store[key] = (expires_at, value)
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    async def delete(self, key: str) -> None:
        """删除单个键。"""
        async with self._lock:
            self._store.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        """按前缀批量删除。"""
        async with self._lock:
            victims = [key for key in self._store if key.startswith(prefix)]
            for key in victims:
                del self._store[key]
            return len(victims)

    async def clear(self) -> None:
        """清空全部条目（测试辅助）。"""
        async with self._lock:
            self._store.clear()

    async def close(self) -> None:
        """释放内存（无外部资源）。"""
        await self.clear()


class RedisCache:
    """Redis 缓存后端；``redis`` 包在构造时延迟导入。

    Args:
        url: Redis DSN。
        default_ttl: 未显式指定 ttl 时使用的秒数。
    """

    def __init__(self, url: str, default_ttl: int = 60) -> None:
        import redis.asyncio as aioredis  # 延迟导入：无 Redis 环境不影响模块加载

        self._client: Any = aioredis.from_url(url, encoding="utf-8", decode_responses=False)
        self._default_ttl = max(1, default_ttl)

    @staticmethod
    def _key(key: str) -> str:
        return f"gd:{key}"

    async def get(self, key: str) -> Any | None:
        """读取并反序列化 JSON 值。"""
        import json

        raw = await self._client.get(self._key(key))
        if raw is None:
            return None
        return json.loads(raw)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """序列化为 JSON 后写入，带 TTL。"""
        import json

        effective_ttl = self._default_ttl if ttl is None else ttl
        payload = json.dumps(value, ensure_ascii=False, default=str)
        expire = effective_ttl if effective_ttl > 0 else None
        await self._client.set(self._key(key), payload, ex=expire)

    async def delete(self, key: str) -> None:
        """删除单个键。"""
        await self._client.delete(self._key(key))

    async def delete_prefix(self, prefix: str) -> int:
        """以 SCAN 游标按前缀批量删除。"""
        removed = 0
        async for raw_key in self._client.scan_iter(match=f"{self._key(prefix)}*", count=500):
            await self._client.delete(raw_key)
            removed += 1
        return removed

    async def ping(self) -> bool:
        """连通性探测，供 ``/ready`` 使用。"""
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        """关闭连接池。"""
        await self._client.aclose()


_cache: CacheBackend | None = None
_cache_lock = asyncio.Lock()


def build_cache(settings: Settings | None = None) -> CacheBackend:
    """按配置构造缓存后端（不缓存实例）。"""
    resolved = settings or get_settings()
    if resolved.cache_backend == "redis":
        if not resolved.redis_url:
            raise ValueError("cache_backend=redis 时必须配置 redis_url")
        return RedisCache(resolved.redis_url, resolved.cache_default_ttl)
    return MemoryCache(resolved.cache_max_entries, resolved.cache_default_ttl)


def get_cache() -> CacheBackend:
    """返回进程内缓存单例，按配置选择后端。"""
    global _cache
    if _cache is None:
        _cache = build_cache()
    return _cache


async def close_cache() -> None:
    """关闭并清空缓存单例（应用 shutdown 调用）。"""
    global _cache
    if _cache is not None:
        await _cache.close()
        _cache = None


def cache_key(*parts: Any) -> str:
    """拼装缓存键，自动跳过空段。"""
    segments = [str(part) for part in parts if part not in (None, "")]
    return ":".join(segments)


__all__ = [
    "MISSING",
    "CacheBackend",
    "MemoryCache",
    "RedisCache",
    "build_cache",
    "cache_key",
    "close_cache",
    "get_cache",
    "get_request_id",
]
