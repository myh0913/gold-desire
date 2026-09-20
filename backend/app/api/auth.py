"""认证路由：验证码、注册、登录、刷新、登出、当前用户。

令牌策略：access token 走响应体（短 TTL），refresh token 走 HttpOnly / Secure /
SameSite=Lax Cookie，路径限定在 ``/api/auth``。
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, Request, Response
from fastapi import status as http_status

from app.api.deps import (
    client_ip,
    get_auth_service,
    get_current_user,
    get_repositories,
)
from app.core.cache import get_cache
from app.core.captcha import issue_captcha
from app.core.config import Settings, get_settings
from app.core.errors import AuthError, RateLimitedError
from app.core.logging import get_request_id
from app.core.pages import effective_pages
from app.core.rate_limit import build_login_limiter
from app.models.auth import User
from app.repositories import Repositories
from app.repositories.auth import RoleRepository
from app.schemas.auth import (
    CaptchaResponse,
    LoginRequest,
    MeResponse,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services.auth_service import AuthService

router = APIRouter(tags=["auth"])

REFRESH_COOKIE_NAME = "gd_refresh"
REFRESH_COOKIE_PATH = "/api/auth"


def set_refresh_cookie(
    response: Response, token: str, settings: Settings | None = None
) -> None:
    """写入 refresh cookie：HttpOnly + Secure + SameSite=Lax + 路径限定。"""
    resolved = settings or get_settings()
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=resolved.refresh_token_days * 86400,
        httponly=True,
        secure=True,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


@router.get("/captcha", response_model=CaptchaResponse)
async def get_captcha() -> CaptchaResponse:
    """生成图形验证码：返回 ``captcha_id`` 与 PNG data URL。"""
    captcha_id, png = await issue_captcha(get_cache())
    encoded = base64.b64encode(png).decode("ascii")
    return CaptchaResponse(captcha_id=captcha_id, image=f"data:image/png;base64,{encoded}")


@router.post(
    "/auth/register", response_model=UserOut, status_code=http_status.HTTP_201_CREATED
)
async def register(
    payload: RegisterRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> UserOut:
    """注册新用户（验证码必填，邀请码可选决定角色）。"""
    user = await service.register(
        payload.username,
        payload.password,
        payload.captcha_id,
        payload.captcha,
        payload.invite_code,
        ip=client_ip(request),
        request_id=get_request_id(),
    )
    return UserOut.model_validate(user)


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """登录：签发 access token 并写入 refresh cookie（含独立登录限流）。"""
    settings = get_settings()
    decision = await build_login_limiter(settings, get_cache()).hit(client_ip(request))
    if not decision.allowed:
        raise RateLimitedError(
            "登录请求过于频繁，请稍后重试",
            code="login_rate_limited",
            detail={"retry_after": decision.retry_after},
        )
    result = await service.login(
        payload.username,
        payload.password,
        ip=client_ip(request),
        request_id=get_request_id(),
    )
    set_refresh_cookie(response, result.refresh_token, settings)
    return TokenResponse(access_token=result.access_token, expires_in=result.expires_in)


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """以 refresh cookie 换取新的 access token（并轮换 refresh）。"""
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token:
        raise AuthError("缺少刷新凭证", code="missing_refresh_token")
    result = await service.refresh(
        token, ip=client_ip(request), request_id=get_request_id()
    )
    set_refresh_cookie(response, result.refresh_token)
    return TokenResponse(access_token=result.access_token, expires_in=result.expires_in)


@router.post("/auth/logout", status_code=http_status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
) -> None:
    """登出：写审计并清除 refresh cookie。"""
    await service.logout(
        username=user.username, ip=client_ip(request), request_id=get_request_id()
    )
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


@router.get("/auth/me", response_model=MeResponse)
async def me(
    user: User = Depends(get_current_user),
    repos: Repositories = Depends(get_repositories),
) -> MeResponse:
    """返回当前用户 + 角色 + 有效页面 key（由注册表派生）。"""
    stored = await RoleRepository(repos.session).get_pages(user.role)
    pages = effective_pages(stored if stored else None, is_admin=(user.role == "admin"))
    return MeResponse(user=UserOut.model_validate(user), role=user.role, pages=pages)


__all__ = [
    "REFRESH_COOKIE_NAME",
    "REFRESH_COOKIE_PATH",
    "router",
    "set_refresh_cookie",
]
