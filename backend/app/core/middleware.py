"""HTTP 中间件：request_id、访问日志、安全响应头、CORS。

以纯 ASGI 中间件实现（不依赖 BaseHTTPMiddleware），避免流式响应被包裹。
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterable
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings, get_settings
from app.core.logging import get_request_id, set_request_id
from app.core.rate_limit import RateLimitMiddleware

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "X-Request-Id"

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    ),
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

_SKIP_ACCESS_LOG_PATHS = frozenset({"/health", "/ready", "/openapi.json", "/docs", "/redoc"})


class RequestIdMiddleware:
    """为每个请求分配/透传 request_id，并写入响应头。"""

    def __init__(self, app: ASGIApp, header_name: str = REQUEST_ID_HEADER) -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = ""
        for key, value in scope.get("headers", []):
            if key.decode("latin-1").lower() == self.header_name.lower():
                incoming = value.decode("latin-1")
                break
        request_id = incoming or uuid.uuid4().hex
        set_request_id(request_id)
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[self.header_name] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            set_request_id(None)


class AccessLogMiddleware:
    """输出访问日志：method / path / status / duration_ms / request_id。"""

    def __init__(self, app: ASGIApp, skip_paths: Iterable[str] = _SKIP_ACCESS_LOG_PATHS) -> None:
        self.app = app
        self.skip_paths = frozenset(skip_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.skip_paths:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_code,
                    "duration_ms": duration_ms,
                    "request_id": get_request_id(),
                    "client": (scope.get("client") or ("", 0))[0],
                },
            )


class SecurityHeadersMiddleware:
    """为所有响应附加安全响应头。"""

    def __init__(self, app: ASGIApp, headers: dict[str, str] | None = None) -> None:
        self.app = app
        self.headers = headers or SECURITY_HEADERS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in self.headers.items():
                    headers.setdefault(key, value)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def build_middlewares() -> list[tuple[Any, dict[str, Any]]]:
    """返回中间件列表（顺序：外层 → 内层）。

    全局限流中间件只作用于 ``/api/*``，并豁免 ``/health`` / ``/ready`` 探针。
    """
    return [
        (RateLimitMiddleware, {}),
        (SecurityHeadersMiddleware, {}),
        (RequestIdMiddleware, {}),
        (AccessLogMiddleware, {}),
    ]


def register_middlewares(app: FastAPI, settings: Settings | None = None) -> None:
    """在 FastAPI 应用上注册中间件与 CORS。"""
    resolved = settings or get_settings()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=resolved.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    for middleware_cls, options in build_middlewares():
        app.add_middleware(middleware_cls, **options)


__all__ = [
    "REQUEST_ID_HEADER",
    "SECURITY_HEADERS",
    "AccessLogMiddleware",
    "RequestIdMiddleware",
    "SecurityHeadersMiddleware",
    "register_middlewares",
]
