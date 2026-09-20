"""限流与登录防爆破（基于 :class:`~app.core.cache.CacheBackend`，无需 Redis）。

包含三部分：

- :class:`RateLimiter`：**命名固定窗口**计数器（``global`` / ``login`` 等桶互不干扰）。
- :class:`LoginGuard`：按 IP 的连续失败锁定（阈值与锁定时长可配）。
- :class:`RateLimitMiddleware`：对 ``/api/*`` 应用全局桶，并**豁免** ``/health`` / ``/ready``。

所有状态经 ``CacheBackend`` 存取，默认 MemoryCache，因此测试无需 Redis 即可运行。
时间统一走可注入的 ``clock``，便于测试锁定到期。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.cache import CacheBackend, get_cache
from app.core.config import Settings, get_settings

Clock = Callable[[], float]

DEFAULT_EXEMPT_PATHS: frozenset[str] = frozenset(
    {"/health", "/ready", "/openapi.json", "/docs", "/redoc", "/favicon.ico"}
)


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """一次限流判定的结果。"""

    allowed: bool
    remaining: int
    retry_after: int


class RateLimiter:
    """命名固定窗口限流器。

    Args:
        cache: 缓存后端（MemoryCache / RedisCache）。
        name: 桶名；不同名字的桶互不影响。
        limit: 窗口内允许的最大请求数。
        window_seconds: 窗口长度（秒）。
        clock: 时间源，便于测试注入。
    """

    def __init__(
        self,
        cache: CacheBackend,
        name: str,
        limit: int,
        window_seconds: int,
        *,
        clock: Clock = time.time,
    ) -> None:
        self._cache = cache
        self.name = name
        self.limit = max(1, limit)
        self.window = max(1, window_seconds)
        self._clock = clock

    def _key(self, bucket_key: str) -> str:
        return f"ratelimit:{self.name}:{bucket_key}"

    async def hit(self, bucket_key: str) -> RateLimitDecision:
        """记录一次请求并返回判定结果（超限时 ``allowed=False``）。"""
        now = self._clock()
        storage_key = self._key(bucket_key)
        state = await self._cache.get(storage_key)
        if not isinstance(state, dict) or float(state.get("expires_at", 0)) <= now:
            state = {"count": 0, "expires_at": now + self.window}
        expires_at = float(state["expires_at"])
        count = int(state["count"]) + 1
        ttl = max(1, round(expires_at - now))
        await self._cache.set(
            storage_key, {"count": count, "expires_at": expires_at}, ttl=ttl
        )
        retry_after = max(0, round(expires_at - now))
        if count > self.limit:
            return RateLimitDecision(allowed=False, remaining=0, retry_after=retry_after)
        remaining = self.limit - count
        return RateLimitDecision(allowed=True, remaining=remaining, retry_after=retry_after)

    async def reset(self, bucket_key: str) -> None:
        """清除某键的计数。"""
        await self._cache.delete(self._key(bucket_key))


class LoginGuard:
    """按 IP 的连续登录失败锁定。

    连续失败达到 ``max_failures`` 次即锁定 ``lockout_seconds`` 秒；期间登录直接
    拒绝并给出剩余秒数。成功登录清零计数。
    """

    def __init__(
        self,
        cache: CacheBackend,
        *,
        max_failures: int = 5,
        lockout_seconds: int = 300,
        window_seconds: int = 300,
        clock: Clock = time.time,
    ) -> None:
        self._cache = cache
        self.max_failures = max(1, max_failures)
        self.lockout_seconds = max(1, lockout_seconds)
        self.window = max(1, window_seconds)
        self._clock = clock

    def _fail_key(self, ip: str) -> str:
        return f"login:fail:{ip}"

    def _lock_key(self, ip: str) -> str:
        return f"login:lock:{ip}"

    async def locked_seconds(self, ip: str) -> int:
        """返回该 IP 剩余锁定秒数，未锁定返回 0。"""
        now = self._clock()
        state = await self._cache.get(self._lock_key(ip))
        if isinstance(state, dict):
            until = float(state.get("until", 0))
            if until > now:
                return round(until - now)
            await self._cache.delete(self._lock_key(ip))
        return 0

    async def record_failure(self, ip: str) -> int:
        """记录一次失败；若本次触发锁定，返回锁定秒数，否则返回 0。"""
        now = self._clock()
        key = self._fail_key(ip)
        state = await self._cache.get(key)
        if not isinstance(state, dict) or float(state.get("expires_at", 0)) <= now:
            state = {"count": 0, "expires_at": now + self.window}
        count = int(state["count"]) + 1
        if count >= self.max_failures:
            await self._cache.set(
                self._lock_key(ip), {"until": now + self.lockout_seconds}, ttl=self.lockout_seconds
            )
            await self._cache.delete(key)
            return self.lockout_seconds
        expires_at = float(state["expires_at"])
        await self._cache.set(
            key,
            {"count": count, "expires_at": expires_at},
            ttl=max(1, round(expires_at - now)),
        )
        return 0

    async def reset(self, ip: str) -> None:
        """成功登录后清零失败计数与锁定。"""
        await self._cache.delete(self._fail_key(ip))
        await self._cache.delete(self._lock_key(ip))


class RateLimitMiddleware:
    """全局 API 限流中间件：仅作用于 ``prefix`` 前缀路径，豁免探针路径。

    Args:
        app: 下游 ASGI 应用。
        settings: 显式配置（测试注入）。
        cache: 显式缓存后端；缺省在首次请求时取进程内单例。
        prefix: 生效路径前缀，默认 ``/api``。
        exempt_paths: 完全豁免的路径集合。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings | None = None,
        cache: CacheBackend | None = None,
        prefix: str = "/api",
        exempt_paths: Iterable[str] = DEFAULT_EXEMPT_PATHS,
    ) -> None:
        self.app = app
        self.settings = settings or get_settings()
        self._cache = cache
        self.prefix = prefix
        self.exempt_paths = frozenset(exempt_paths)
        self._limiter: RateLimiter | None = None

    def _resolve_limiter(self) -> RateLimiter:
        if self._limiter is None:
            cache = self._cache or get_cache()
            self._limiter = RateLimiter(
                cache,
                "global",
                self.settings.rate_limit_requests,
                self.settings.rate_limit_window_seconds,
            )
        return self._limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        if not path.startswith(self.prefix) or path in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        client = (scope.get("client") or ("unknown", 0))[0] or "unknown"
        decision = await self._resolve_limiter().hit(str(client))
        if not decision.allowed:
            response = JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "请求过于频繁，请稍后重试",
                        "detail": {"retry_after": decision.retry_after},
                    }
                },
                headers={"Retry-After": str(decision.retry_after)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def build_global_limiter(
    settings: Settings | None = None, cache: CacheBackend | None = None
) -> RateLimiter:
    """构造全局 API 限流器（供依赖或中间件复用）。"""
    resolved = settings or get_settings()
    return RateLimiter(
        cache or get_cache(),
        "global",
        resolved.rate_limit_requests,
        resolved.rate_limit_window_seconds,
    )


def build_login_limiter(
    settings: Settings | None = None, cache: CacheBackend | None = None
) -> RateLimiter:
    """构造登录接口独立限流器（与全局桶互不干扰）。"""
    resolved = settings or get_settings()
    return RateLimiter(
        cache or get_cache(),
        "login",
        resolved.login_rate_limit_requests,
        resolved.rate_limit_window_seconds,
    )


__all__ = [
    "DEFAULT_EXEMPT_PATHS",
    "Clock",
    "LoginGuard",
    "RateLimitDecision",
    "RateLimitMiddleware",
    "RateLimiter",
    "build_global_limiter",
    "build_login_limiter",
]
