"""角色与邀请码服务。

- :class:`RoleService`：角色 CRUD 与页面权限矩阵读写。页面 key 一律对照
  :mod:`app.core.pages` 注册表校验，未知 key 直接拒绝；读取时矩阵恒由注册表
  派生，故新增页面会自动出现，绝不静默丢失。
- :class:`InvitationService`：邀请码创建/列出/吊销/消费（注册时一次性使用）。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.errors import NotFoundError, ValidationError
from app.core.pages import effective_pages, validate_page_keys
from app.core.pages import page_matrix as derive_page_matrix
from app.models.auth import Invitation, Role
from app.repositories import Repositories
from app.repositories.auth import InvitationRepository, RoleRepository, UserRepository

BUILTIN_ROLES: dict[str, str] = {
    "admin": "管理员",
    "analyst": "分析师",
    "viewer": "访客",
}
"""内置角色：不可删除、不可改权限（admin 恒为全部页面）。"""


def _as_utc(value: datetime) -> datetime:
    """把可能缺失时区的 datetime 归一为 UTC 感知时间（兼容 SQLite）。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RoleInfo:
    """角色视图对象。"""

    name: str
    label: str
    is_builtin: bool
    pages: list[str]
    pages_configured: bool


class RoleService:
    """角色与页面权限服务。"""

    def __init__(self, repos: Repositories) -> None:
        self.repos = repos
        self.roles = RoleRepository(repos.session)
        self.users = UserRepository(repos.session)
        self.audit = repos.audit_logs

    async def ensure_builtin_roles(self) -> None:
        """幂等确保三个内置角色存在。"""
        for name, label in BUILTIN_ROLES.items():
            if await self.roles.get(name) is None:
                await self.roles.create(name, label, is_builtin=True)

    async def list_roles(self) -> list[RoleInfo]:
        """列出全部角色（含由注册表派生的页面权限）。"""
        await self.ensure_builtin_roles()
        return [await self._view(role) for role in await self.roles.list_roles()]

    async def get_role(self, name: str) -> RoleInfo:
        """取单个角色视图。"""
        await self.ensure_builtin_roles()
        role = await self.roles.get(name)
        if role is None:
            raise NotFoundError("角色不存在", detail={"role": name})
        return await self._view(role)

    async def create_role(self, actor: str, name: str, label: str) -> RoleInfo:
        """新建自定义角色并写审计。"""
        await self.ensure_builtin_roles()
        if await self.roles.get(name) is not None:
            raise ValidationError("角色已存在", code="role_exists", detail={"role": name})
        role = await self.roles.create(name, label, is_builtin=False)
        await self.audit.record(
            actor=actor, action="role_created", target=name, detail={"label": label}
        )
        return await self._view(role)

    async def delete_role(self, actor: str, name: str) -> None:
        """删除自定义角色；内置角色不可删除，仍被用户使用时拒绝。"""
        await self.ensure_builtin_roles()
        role = await self.roles.get(name)
        if role is None:
            raise NotFoundError("角色不存在", detail={"role": name})
        if role.is_builtin:
            raise ValidationError("内置角色不可删除", code="builtin_role")
        if await self.users.list_by_role(name):
            raise ValidationError("仍有用户使用该角色，无法删除", code="role_in_use")
        await self.roles.delete_role(role)
        await self.audit.record(actor=actor, action="role_deleted", target=name)

    async def get_pages(self, name: str) -> list[str]:
        """取角色**有效**页面集合（由注册表派生）。"""
        return (await self.get_role(name)).pages

    async def page_matrix(self, name: str) -> dict[str, bool]:
        """取角色完整页面矩阵（行集恒等于注册表）。"""
        role = await self.roles.get(name)
        if role is None:
            raise NotFoundError("角色不存在", detail={"role": name})
        stored = await self.roles.get_pages(name)
        return derive_page_matrix(stored if stored else None, is_admin=(name == "admin"))

    async def set_pages(self, actor: str, name: str, page_keys: list[str]) -> RoleInfo:
        """覆盖角色页面权限；未知 key 抛 :class:`ValidationError`。"""
        role = await self.roles.get(name)
        if role is None:
            raise NotFoundError("角色不存在", detail={"role": name})
        validated = validate_page_keys(page_keys)
        await self.roles.set_pages(name, validated)
        await self.audit.record(
            actor=actor, action="role_pages_updated", target=name, detail={"pages": validated}
        )
        return await self._view(role)

    async def reset_pages(self, actor: str, name: str) -> RoleInfo:
        """清空角色页面权限，回退到注册表默认。"""
        role = await self.roles.get(name)
        if role is None:
            raise NotFoundError("角色不存在", detail={"role": name})
        await self.roles.clear_pages(name)
        await self.audit.record(actor=actor, action="role_pages_reset", target=name)
        return await self._view(role)

    async def _view(self, role: Role) -> RoleInfo:
        """由 ORM 角色行构造视图对象。"""
        name = role.name
        stored = await self.roles.get_pages(name)
        pages = effective_pages(stored if stored else None, is_admin=(name == "admin"))
        return RoleInfo(
            name=name,
            label=role.label,
            is_builtin=role.is_builtin,
            pages=pages,
            pages_configured=bool(stored),
        )


class InvitationService:
    """邀请码服务：注册时凭码决定角色，一次性使用。"""

    def __init__(self, repos: Repositories) -> None:
        self.repos = repos
        self.invitations = InvitationRepository(repos.session)
        self.roles = RoleRepository(repos.session)
        self.audit = repos.audit_logs

    async def create(self, actor: str, role: str, expires_in_days: int) -> Invitation:
        """创建邀请码：有效期 1~365 天，角色须存在。"""
        if not 1 <= expires_in_days <= 365:
            raise ValidationError("有效期须在 1~365 天之间", code="invalid_expiry")
        await RoleService(self.repos).ensure_builtin_roles()
        if await self.roles.get(role) is None:
            raise ValidationError("角色不存在", code="role_not_found", detail={"role": role})
        code = secrets.token_urlsafe(12)
        expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)
        invitation = await self.invitations.create(code, role, expires_at)
        await self.audit.record(
            actor=actor,
            action="invitation_created",
            target=code,
            detail={"role": role, "expires_in_days": expires_in_days},
        )
        return invitation

    async def list_invitations(self) -> list[Invitation]:
        """列出全部邀请码。"""
        return list(await self.invitations.list_invitations())

    async def revoke(self, actor: str, code: str) -> Invitation:
        """吊销邀请码。"""
        invitation = await self.invitations.get_by_code(code)
        if invitation is None:
            raise NotFoundError("邀请码不存在", detail={"code": code})
        invitation.revoked = True
        await self.repos.session.flush()
        await self.audit.record(actor=actor, action="invitation_revoked", target=code)
        return invitation

    async def consume(self, code: str, username: str) -> Invitation:
        """消费邀请码（注册路径）；无效/已用/已吊销/已过期均拒绝。"""
        invitation = await self.invitations.get_by_code(code)
        if invitation is None:
            raise ValidationError("邀请码无效", code="invalid_invitation")
        if invitation.revoked:
            raise ValidationError("邀请码已吊销", code="invitation_revoked")
        if invitation.used_by is not None:
            raise ValidationError("邀请码已被使用", code="invitation_used")
        if invitation.expires_at is not None and _as_utc(invitation.expires_at) < datetime.now(UTC):
            raise ValidationError("邀请码已过期", code="invitation_expired")
        invitation.used_by = username
        invitation.used_at = datetime.now(UTC)
        await self.repos.session.flush()
        return invitation


__all__ = ["BUILTIN_ROLES", "InvitationService", "RoleInfo", "RoleService"]
