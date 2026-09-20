"""认证域仓储：用户 / 角色 / 页面权限 / 邀请码的唯一 DB 读写出口。

本模块为**纯新增**模块（不修改既有模型与仓储），补齐 RBAC 所需的访问方法，
供 :mod:`app.services.auth_service` / :mod:`app.services.user_service` /
:mod:`app.services.role_service` 使用。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select

from app.models.auth import Invitation, Role, RolePage, User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    """用户表访问。"""

    async def get_by_username(self, username: str) -> User | None:
        """按用户名取用户，不存在返回 ``None``。"""
        return await self.session.scalar(select(User).where(User.username == username))

    async def list_users(self) -> list[User]:
        """列出全部用户，按 id 升序。"""
        result = await self.session.execute(select(User).order_by(User.id))
        return list(result.scalars().all())

    async def list_by_role(self, role: str) -> list[User]:
        """列出某角色的全部用户。"""
        result = await self.session.execute(select(User).where(User.role == role))
        return list(result.scalars().all())

    async def count_enabled_admins(self) -> int:
        """统计「启用中的管理员」数量（用于最后一个管理员保护）。"""
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.enabled.is_(True))
        )
        return int(await self.session.scalar(stmt) or 0)

    async def create(
        self, username: str, password_hash: str, role: str, *, enabled: bool = True
    ) -> User:
        """新建用户并 flush，返回该行。"""
        row = User(
            username=username, password_hash=password_hash, role=role, enabled=enabled
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete_user(self, user: User) -> None:
        """删除用户行。"""
        await self.session.delete(user)
        await self.session.flush()

    async def touch_last_login(self, user: User, when: datetime) -> None:
        """更新最近登录时间。"""
        user.last_login_at = when
        await self.session.flush()


class RoleRepository(BaseRepository):
    """角色与页面权限矩阵访问。"""

    async def get(self, name: str) -> Role | None:
        """按角色名取角色，不存在返回 ``None``。"""
        return await self.session.get(Role, name)

    async def list_roles(self) -> list[Role]:
        """列出全部角色：内置优先，其后按名称排序。"""
        stmt = select(Role).order_by(Role.is_builtin.desc(), Role.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, name: str, label: str, *, is_builtin: bool = False) -> Role:
        """新建角色并 flush，返回该行。"""
        row = Role(name=name, label=label, is_builtin=is_builtin)
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete_role(self, role: Role) -> None:
        """删除角色行（其页面权限经外键级联删除）。"""
        await self.session.delete(role)
        await self.session.flush()

    async def get_pages(self, role: str) -> list[str]:
        """取角色已持久化的页面 key，按 key 排序。"""
        stmt = (
            select(RolePage.page_key)
            .where(RolePage.role == role)
            .order_by(RolePage.page_key)
        )
        result = await self.session.execute(stmt)
        return [str(row) for row in result.scalars().all()]

    async def set_pages(self, role: str, page_keys: Sequence[str]) -> None:
        """整体覆盖角色的页面权限（先清后写）。"""
        await self.delete_where(RolePage, RolePage.role == role)
        for key in page_keys:
            self.session.add(RolePage(role=role, page_key=str(key)))
        await self.session.flush()

    async def clear_pages(self, role: str) -> None:
        """清空角色页面权限（回退到注册表默认）。"""
        await self.delete_where(RolePage, RolePage.role == role)


class InvitationRepository(BaseRepository):
    """邀请码访问。"""

    async def get_by_code(self, code: str) -> Invitation | None:
        """按邀请码取记录，不存在返回 ``None``。"""
        return await self.session.scalar(select(Invitation).where(Invitation.code == code))

    async def list_invitations(self) -> list[Invitation]:
        """列出全部邀请码，按创建时间倒序。"""
        stmt = select(Invitation).order_by(Invitation.id.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, code: str, role: str, expires_at: datetime | None) -> Invitation:
        """新建邀请码并 flush，返回该行。"""
        row = Invitation(code=code, role=role, expires_at=expires_at)
        self.session.add(row)
        await self.session.flush()
        return row


__all__ = ["InvitationRepository", "RoleRepository", "UserRepository"]
