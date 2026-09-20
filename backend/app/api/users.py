"""管理员用户管理路由（仅 admin，且需「设置」页面权限）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_user_service, require_admin, require_page
from app.core.errors import ValidationError
from app.core.pages import PageKey
from app.models.auth import User
from app.schemas.auth import PasswordReset, UserCreate, UserOut, UserUpdate
from app.services.user_service import UserService

router = APIRouter(
    prefix="/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(require_admin), Depends(require_page(PageKey.SETTINGS))],
)


@router.get("", response_model=list[UserOut])
async def list_users(
    _: User = Depends(require_admin),
    service: UserService = Depends(get_user_service),
) -> list[UserOut]:
    """列出全部用户。"""
    return [UserOut.model_validate(user) for user in await service.list_users()]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    actor: User = Depends(require_admin),
    service: UserService = Depends(get_user_service),
) -> UserOut:
    """创建用户。"""
    user = await service.create_user(
        actor.username, payload.username, payload.password, payload.role
    )
    return UserOut.model_validate(user)


@router.patch("/{username}", response_model=UserOut)
async def update_user(
    username: str,
    payload: UserUpdate,
    actor: User = Depends(require_admin),
    service: UserService = Depends(get_user_service),
) -> UserOut:
    """更新用户角色与/或启用状态。"""
    if payload.role is None and payload.enabled is None:
        raise ValidationError("未提供任何可更新字段", code="empty_update")
    user = await service.get_user(username)
    if payload.role is not None:
        user = await service.update_role(actor.username, username, payload.role)
    if payload.enabled is not None:
        user = await service.set_enabled(actor.username, username, payload.enabled)
    return UserOut.model_validate(user)


@router.post("/{username}/password", response_model=UserOut)
async def reset_password(
    username: str,
    payload: PasswordReset,
    actor: User = Depends(require_admin),
    service: UserService = Depends(get_user_service),
) -> UserOut:
    """重置用户密码。"""
    user = await service.reset_password(actor.username, username, payload.password)
    return UserOut.model_validate(user)


@router.delete("/{username}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    username: str,
    actor: User = Depends(require_admin),
    service: UserService = Depends(get_user_service),
) -> None:
    """删除用户。"""
    await service.delete_user(actor.username, username)


__all__ = ["router"]
