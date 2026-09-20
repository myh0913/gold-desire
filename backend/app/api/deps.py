"""API 依赖：当前用户、角色/管理员校验、页面级校验与服务工厂。

双层授权（服务端为准）：

- 接口级：:func:`require_role` / :func:`require_admin`；
- 路由级（页面可访问性）：:func:`require_page`。

未认证一律 401（:class:`AuthError`）；已认证但越权一律 403
（:class:`PermissionDeniedError`）**并写入审计日志**。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.cache import get_cache
from app.core.errors import AuthError, PermissionDeniedError
from app.core.logging import get_request_id
from app.core.pages import PageKey, effective_pages
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.models.auth import User
from app.repositories import Repositories, get_repositories
from app.repositories.auth import RoleRepository, UserRepository
from app.services.auth_service import AuthService
from app.services.role_service import InvitationService, RoleService
from app.services.user_service import UserService

_bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    """从请求中取客户端 IP（取不到返回 ``unknown``）。"""
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    repos: Repositories = Depends(get_repositories),
) -> User:
    """校验 access token、加载用户并拒绝停用用户。

    Raises:
        AuthError: 缺少/无效 access token，或用户不存在/已停用（401）。
    """
    if credentials is None or not credentials.credentials:
        raise AuthError("未登录或缺少凭证")
    payload = decode_token(credentials.credentials, expected_type=TOKEN_TYPE_ACCESS)
    username = str(payload["sub"])
    user = await UserRepository(repos.session).get_by_username(username)
    if user is None or not user.enabled:
        raise AuthError("用户不存在或已停用")
    request.state.user = user
    return user


async def record_permission_denied(
    repos: Repositories,
    request: Request,
    actor: str | None,
    target: str,
    detail: dict[str, object] | None = None,
) -> None:
    """写入越权审计并**立即提交**，避免 403 返回时事务回滚丢失审计。"""
    await repos.audit_logs.record(
        actor=actor,
        action="permission_denied",
        target=target,
        detail=detail,
        ip=client_ip(request),
        request_id=get_request_id(),
    )
    await repos.session.commit()


def require_role(*roles: str) -> Callable[..., Awaitable[User]]:
    """构造「需具备指定角色之一」的依赖。"""

    async def _dependency(
        request: Request,
        user: User = Depends(get_current_user),
        repos: Repositories = Depends(get_repositories),
    ) -> User:
        if user.role not in roles:
            await record_permission_denied(
                repos,
                request,
                user.username,
                request.url.path,
                {"required_roles": list(roles), "actual_role": user.role},
            )
            raise PermissionDeniedError("权限不足", code="role_required")
        return user

    return _dependency


async def require_admin(
    request: Request,
    user: User = Depends(get_current_user),
    repos: Repositories = Depends(get_repositories),
) -> User:
    """要求管理员角色；越权返回 403 并写审计。"""
    if user.role != "admin":
        await record_permission_denied(
            repos,
            request,
            user.username,
            request.url.path,
            {"required_roles": ["admin"], "actual_role": user.role},
        )
        raise PermissionDeniedError("需要管理员权限", code="admin_required")
    return user


def require_page(page: PageKey) -> Callable[..., Awaitable[User]]:
    """构造「角色需具备该页面权限」的依赖（管理员恒通过）。"""

    async def _dependency(
        request: Request,
        user: User = Depends(get_current_user),
        repos: Repositories = Depends(get_repositories),
    ) -> User:
        if user.role == "admin":
            return user
        stored = await RoleRepository(repos.session).get_pages(user.role)
        granted = effective_pages(stored if stored else None, is_admin=False)
        if page.value not in granted:
            await record_permission_denied(
                repos,
                request,
                user.username,
                page.value,
                {"required_page": page.value, "role": user.role},
            )
            raise PermissionDeniedError(
                f"无权访问页面：{page.value}", code="page_forbidden", detail={"page": page.value}
            )
        return user

    return _dependency


def get_auth_service(repos: Repositories = Depends(get_repositories)) -> AuthService:
    """请求级认证服务。"""
    return AuthService(repos, cache=get_cache())


def get_user_service(repos: Repositories = Depends(get_repositories)) -> UserService:
    """请求级用户服务。"""
    return UserService(repos)


def get_role_service(repos: Repositories = Depends(get_repositories)) -> RoleService:
    """请求级角色服务。"""
    return RoleService(repos)


def get_invitation_service(repos: Repositories = Depends(get_repositories)) -> InvitationService:
    """请求级邀请码服务。"""
    return InvitationService(repos)


__all__ = [
    "client_ip",
    "get_auth_service",
    "get_current_user",
    "get_invitation_service",
    "get_role_service",
    "get_user_service",
    "record_permission_denied",
    "require_admin",
    "require_page",
    "require_role",
]
