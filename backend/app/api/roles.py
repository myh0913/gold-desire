"""管理员角色与页面权限路由（仅 admin，且需「设置」页面权限）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_role_service, require_admin, require_page
from app.core.pages import PageKey
from app.models.auth import User
from app.schemas.auth import RoleCreate, RoleOut, RolePagesUpdate
from app.services.role_service import RoleInfo, RoleService

router = APIRouter(
    prefix="/admin/roles",
    tags=["admin-roles"],
    dependencies=[Depends(require_admin), Depends(require_page(PageKey.SETTINGS))],
)


def _to_out(info: RoleInfo) -> RoleOut:
    """把角色视图对象转为响应 DTO。"""
    return RoleOut(
        name=info.name,
        label=info.label,
        is_builtin=info.is_builtin,
        pages=info.pages,
        pages_configured=info.pages_configured,
    )


@router.get("", response_model=list[RoleOut])
async def list_roles(
    _: User = Depends(require_admin),
    service: RoleService = Depends(get_role_service),
) -> list[RoleOut]:
    """列出全部角色（含注册表派生的页面权限）。"""
    return [_to_out(info) for info in await service.list_roles()]


@router.post("", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    actor: User = Depends(require_admin),
    service: RoleService = Depends(get_role_service),
) -> RoleOut:
    """新建自定义角色。"""
    return _to_out(await service.create_role(actor.username, payload.name, payload.label))


@router.get("/{name}", response_model=RoleOut)
async def get_role(
    name: str,
    _: User = Depends(require_admin),
    service: RoleService = Depends(get_role_service),
) -> RoleOut:
    """取单个角色。"""
    return _to_out(await service.get_role(name))


@router.put("/{name}/pages", response_model=RoleOut)
async def set_role_pages(
    name: str,
    payload: RolePagesUpdate,
    actor: User = Depends(require_admin),
    service: RoleService = Depends(get_role_service),
) -> RoleOut:
    """覆盖角色页面权限；未知页面 key 一律拒绝。"""
    return _to_out(await service.set_pages(actor.username, name, payload.pages))


@router.post("/{name}/pages/reset", response_model=RoleOut)
async def reset_role_pages(
    name: str,
    actor: User = Depends(require_admin),
    service: RoleService = Depends(get_role_service),
) -> RoleOut:
    """重置角色页面权限为注册表默认。"""
    return _to_out(await service.reset_pages(actor.username, name))


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    name: str,
    actor: User = Depends(require_admin),
    service: RoleService = Depends(get_role_service),
) -> None:
    """删除自定义角色（内置角色不可删除）。"""
    await service.delete_role(actor.username, name)


__all__ = ["router"]
