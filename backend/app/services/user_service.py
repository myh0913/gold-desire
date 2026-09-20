"""用户服务：用户 CRUD、角色调整、启停、改密，并保护最后一个启用管理员。

安全不变量（服务端强制）：

- 最后一个**启用中的**管理员不可被降级 / 停用 / 删除；
- 任何用户不可对**自身**执行降级 / 停用 / 删除。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.core.security import hash_password, validate_password_strength
from app.models.auth import User
from app.repositories import Repositories
from app.repositories.auth import RoleRepository, UserRepository


class UserService:
    """用户管理服务（仅管理员路由调用）。"""

    def __init__(self, repos: Repositories, *, settings: Settings | None = None) -> None:
        self.repos = repos
        self.settings = settings or get_settings()
        self.users = UserRepository(repos.session)
        self.roles = RoleRepository(repos.session)
        self.audit = repos.audit_logs

    async def list_users(self) -> list[User]:
        """列出全部用户。"""
        return list(await self.users.list_users())

    async def get_user(self, username: str) -> User:
        """按用户名取用户。"""
        return await self._target(username)

    async def create_user(self, actor: str, username: str, password: str, role: str) -> User:
        """新建用户：校验密码强度、用户名唯一、角色存在。"""
        validate_password_strength(password)
        if await self.users.get_by_username(username) is not None:
            raise ValidationError("用户名已存在", code="username_taken")
        if await self.roles.get(role) is None:
            raise ValidationError("角色不存在", code="role_not_found", detail={"role": role})
        user = await self.users.create(
            username, hash_password(password, settings=self.settings), role
        )
        await self.audit.record(
            actor=actor, action="user_created", target=username, detail={"role": role}
        )
        return user

    async def update_role(self, actor: str, username: str, new_role: str) -> User:
        """调整用户角色，保护自身与最后一个启用管理员。"""
        user = await self._target(username)
        self._forbid_self(actor, username, "降级")
        if await self.roles.get(new_role) is None:
            raise ValidationError("角色不存在", code="role_not_found", detail={"role": new_role})
        if user.role != new_role and user.role == "admin":
            await self._forbid_last_admin(user)
        previous = user.role
        user.role = new_role
        await self.repos.session.flush()
        await self.audit.record(
            actor=actor,
            action="user_role_changed",
            target=username,
            detail={"from": previous, "to": new_role},
        )
        return user

    async def set_enabled(self, actor: str, username: str, enabled: bool) -> User:
        """启用/停用用户，保护自身与最后一个启用管理员。"""
        user = await self._target(username)
        self._forbid_self(actor, username, "停用" if not enabled else "启用")
        if not enabled and user.role == "admin":
            await self._forbid_last_admin(user)
        user.enabled = enabled
        await self.repos.session.flush()
        await self.audit.record(
            actor=actor,
            action="user_enabled" if enabled else "user_disabled",
            target=username,
        )
        return user

    async def reset_password(self, actor: str, username: str, password: str) -> User:
        """重置用户密码。"""
        user = await self._target(username)
        validate_password_strength(password)
        user.password_hash = hash_password(password, settings=self.settings)
        await self.repos.session.flush()
        await self.audit.record(actor=actor, action="user_password_reset", target=username)
        return user

    async def delete_user(self, actor: str, username: str) -> None:
        """删除用户，保护自身与最后一个启用管理员。"""
        user = await self._target(username)
        self._forbid_self(actor, username, "删除")
        if user.role == "admin":
            await self._forbid_last_admin(user)
        await self.users.delete_user(user)
        await self.audit.record(actor=actor, action="user_deleted", target=username)

    async def _target(self, username: str) -> User:
        """取目标用户，不存在抛 :class:`NotFoundError`。"""
        user = await self.users.get_by_username(username)
        if user is None:
            raise NotFoundError("用户不存在", detail={"username": username})
        return user

    @staticmethod
    def _forbid_self(actor: str, username: str, action: str) -> None:
        """禁止对自身执行敏感操作。"""
        if actor == username:
            raise PermissionDeniedError(
                f"不能对自身执行{action}操作", code="self_operation_forbidden"
            )

    async def _forbid_last_admin(self, user: User) -> None:
        """若目标为最后一个启用管理员，则拒绝降级/停用/删除。"""
        if user.enabled and await self.users.count_enabled_admins() <= 1:
            raise PermissionDeniedError(
                "最后一个启用中的管理员不可被降级、停用或删除",
                code="last_admin_protected",
            )


__all__ = ["UserService"]
