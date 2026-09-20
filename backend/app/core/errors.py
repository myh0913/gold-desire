"""领域异常层次与全局异常处理器。

所有业务错误统一继承 :class:`AppError`，经处理器转换为一致的错误信封::

    {"error": {"code": "not_found", "message": "股票不存在", "detail": {...}}}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_id

logger = logging.getLogger(__name__)


class AppError(Exception):
    """业务异常基类。

    Args:
        message: 面向用户的中文可读信息。
        code: 稳定的机器可读错误码。
        status_code: HTTP 状态码。
        detail: 附加结构化上下文（会写入日志，是否返回由子类决定）。
    """

    code: str = "app_error"
    status_code: int = 500

    def __init__(
        self,
        message: str = "服务内部错误",
        *,
        code: str | None = None,
        status_code: int | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.detail = detail

    def to_envelope(self) -> dict[str, Any]:
        """构造标准错误信封。"""
        return {"error": {"code": self.code, "message": self.message, "detail": self.detail}}


class NotFoundError(AppError):
    """资源不存在。"""

    code = "not_found"
    status_code = 404


class ValidationError(AppError):
    """入参或业务规则校验失败。"""

    code = "validation_error"
    status_code = 422


class PermissionDeniedError(AppError):
    """已认证但无权限。"""

    code = "permission_denied"
    status_code = 403


class AuthError(AppError):
    """未认证或凭证失效。"""

    code = "unauthorized"
    status_code = 401


class UpstreamError(AppError):
    """上游数据源调用失败。"""

    code = "upstream_error"
    status_code = 502


class SnapshotMissingError(AppError):
    """回放模式下所需历史快照缺失；禁止回退到实时源。"""

    code = "snapshot_missing"
    status_code = 409


class RateLimitedError(AppError):
    """触发限流或登录锁定。"""

    code = "rate_limited"
    status_code = 429


def _envelope(code: str, message: str, detail: Any, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "detail": detail}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI 应用上注册全局异常处理器。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "app_error",
            extra={
                "code": exc.code,
                "status_code": exc.status_code,
                "path": request.url.path,
                "detail": exc.detail,
                "request_id": get_request_id(),
            },
        )
        return _envelope(exc.code, exc.message, exc.detail, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _envelope(
            "validation_error",
            "请求参数校验失败",
            exc.errors(),
            422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _envelope(
            "http_error",
            str(exc.detail),
            {"path": request.url.path},
            exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            extra={"path": request.url.path, "request_id": get_request_id()},
        )
        return _envelope("internal_error", "服务内部错误", None, 500)


__all__ = [
    "AppError",
    "AuthError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitedError",
    "SnapshotMissingError",
    "UpstreamError",
    "ValidationError",
    "register_exception_handlers",
]
