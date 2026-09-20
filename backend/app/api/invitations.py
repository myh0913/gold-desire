"""管理员邀请码路由（仅 admin，且需「设置」页面权限）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_invitation_service, require_admin, require_page
from app.core.pages import PageKey
from app.models.auth import User
from app.schemas.auth import InvitationCreate, InvitationOut
from app.services.role_service import InvitationService

router = APIRouter(
    prefix="/admin/invitations",
    tags=["admin-invitations"],
    dependencies=[Depends(require_admin), Depends(require_page(PageKey.SETTINGS))],
)


@router.get("", response_model=list[InvitationOut])
async def list_invitations(
    _: User = Depends(require_admin),
    service: InvitationService = Depends(get_invitation_service),
) -> list[InvitationOut]:
    """列出全部邀请码。"""
    return [
        InvitationOut.model_validate(item) for item in await service.list_invitations()
    ]


@router.post("", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    payload: InvitationCreate,
    actor: User = Depends(require_admin),
    service: InvitationService = Depends(get_invitation_service),
) -> InvitationOut:
    """创建邀请码（有效期 1~365 天）。"""
    invitation = await service.create(actor.username, payload.role, payload.expires_in_days)
    return InvitationOut.model_validate(invitation)


@router.delete("/{code}", response_model=InvitationOut)
async def revoke_invitation(
    code: str,
    actor: User = Depends(require_admin),
    service: InvitationService = Depends(get_invitation_service),
) -> InvitationOut:
    """吊销邀请码。"""
    invitation = await service.revoke(actor.username, code)
    return InvitationOut.model_validate(invitation)


__all__ = ["router"]
