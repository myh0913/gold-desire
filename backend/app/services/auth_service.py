"""认证服务：注册、登录、刷新、登出，含验证码校验与登录防爆破。

- 注册要求图形验证码（缓存态、一次性），可选邀请码决定角色；
- 登录失败按 IP 累计，达到阈值即锁定并返回明确的剩余秒数；
- 登录 / 登出 / 失败 / 锁定均写入审计日志。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.cache import CacheBackend, get_cache
from app.core.captcha import consume_captcha
from app.core.config import Settings, get_settings
from app.core.errors import AuthError, RateLimitedError, ValidationError
from app.core.rate_limit import Clock, LoginGuard
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.models.auth import User
from app.repositories import Repositories
from app.repositories.auth import InvitationRepository, RoleRepository, UserRepository
from app.services.role_service import InvitationService, RoleService

DEFAULT_ROLE = "viewer"
"""未携带邀请码时的默认角色。"""


@dataclass(frozen=True, slots=True)
class AuthResult:
    """登录/刷新的结果：access token + 轮换后的 refresh token + 用户。"""

    access_token: str
    refresh_token: str
    expires_in: int
    user: User


class AuthService:
    """认证服务。

    Args:
        repos: 请求级仓储容器。
        cache: 缓存后端；缺省取进程内单例。
        settings: 显式配置（测试注入）。
        clock: 时间源，用于登录锁定到期判定（测试注入）。
    """

    def __init__(
        self,
        repos: Repositories,
        *,
        cache: CacheBackend | None = None,
        settings: Settings | None = None,
        clock: Clock = time.time,
    ) -> None:
        self.repos = repos
        self.settings = settings or get_settings()
        self.cache = cache or get_cache()
        self.clock = clock
        self.users = UserRepository(repos.session)
        self.roles = RoleRepository(repos.session)
        self.invitations = InvitationRepository(repos.session)
        self.audit = repos.audit_logs
        self.guard = LoginGuard(
            self.cache,
            max_failures=self.settings.login_max_failures,
            lockout_seconds=self.settings.login_lockout_minutes * 60,
            clock=clock,
        )

    # ------------------------------------------------------------------ 注册

    async def register(
        self,
        username: str,
        password: str,
        captcha_id: str,
        captcha: str,
        invite_code: str | None = None,
        *,
        ip: str | None = None,
        request_id: str | None = None,
    ) -> User:
        """注册新用户：验证码必填、密码强度校验、用户名唯一、邀请码一次性。"""
        validate_password_strength(password)
        if not await consume_captcha(self.cache, captcha_id, captcha):
            raise ValidationError("验证码错误或已过期", code="invalid_captcha")
        if await self.users.get_by_username(username) is not None:
            raise ValidationError("用户名已存在", code="username_taken")

        role_name = DEFAULT_ROLE
        invited = False
        if invite_code:
            invitation = await InvitationService(self.repos).consume(invite_code, username)
            role_name = invitation.role
            invited = True

        await RoleService(self.repos).ensure_builtin_roles()
        user = await self.users.create(
            username, hash_password(password, settings=self.settings), role_name
        )
        await self.audit.record(
            actor=username,
            action="user_registered",
            target=username,
            detail={"role": role_name, "invited": invited},
            ip=ip,
            request_id=request_id,
        )
        return user

    # ------------------------------------------------------------------ 登录

    async def login(
        self,
        username: str,
        password: str,
        *,
        ip: str | None = None,
        request_id: str | None = None,
    ) -> AuthResult:
        """校验凭证并签发令牌；失败累计、达阈值锁定。"""
        ip_key = ip or "unknown"

        locked = await self.guard.locked_seconds(ip_key)
        if locked > 0:
            await self._audit_and_commit(
                "login_blocked", username, {"retry_after": locked}, ip, request_id
            )
            raise RateLimitedError(
                f"登录失败次数过多，请 {locked} 秒后重试",
                code="login_locked",
                detail={"retry_after": locked},
            )

        user = await self.users.get_by_username(username)
        authenticated = (
            user is not None
            and user.enabled
            and verify_password(password, user.password_hash, settings=self.settings)
        )
        if not authenticated:
            remaining = await self.guard.record_failure(ip_key)
            if remaining > 0:
                await self._audit_and_commit(
                    "login_locked", username, {"retry_after": remaining}, ip, request_id
                )
                raise RateLimitedError(
                    f"登录失败次数过多，账号已锁定 {remaining} 秒",
                    code="login_locked",
                    detail={"retry_after": remaining},
                )
            await self._audit_and_commit("login_failed", username, None, ip, request_id)
            raise AuthError("用户名或密码错误")

        assert user is not None  # 认证成功时必然非空
        await self.guard.reset(ip_key)
        user.last_login_at = datetime.now(UTC)
        if needs_rehash(user.password_hash, settings=self.settings):
            user.password_hash = hash_password(password, settings=self.settings)
        await self.repos.session.flush()
        await self.audit.record(
            actor=username, action="login", target=username, ip=ip, request_id=request_id
        )
        return self._issue(user)

    # ------------------------------------------------------------ 刷新 / 登出

    async def refresh(
        self, refresh_token: str, *, ip: str | None = None, request_id: str | None = None
    ) -> AuthResult:
        """以 refresh token 换取新的 access token（并轮换 refresh）。"""
        payload = decode_token(
            refresh_token, expected_type=TOKEN_TYPE_REFRESH, settings=self.settings
        )
        username = str(payload["sub"])
        user = await self.users.get_by_username(username)
        if user is None or not user.enabled:
            raise AuthError("用户不存在或已停用")
        await self.audit.record(
            actor=username, action="token_refreshed", target=username, ip=ip, request_id=request_id
        )
        return self._issue(user)

    async def logout(
        self, *, username: str, ip: str | None = None, request_id: str | None = None
    ) -> None:
        """登出：写审计（Cookie 清除由路由层完成）。"""
        await self._audit_and_commit("logout", username, None, ip, request_id)

    # ------------------------------------------------------------------ 内部

    def _issue(self, user: User) -> AuthResult:
        """为用户签发 access + refresh 令牌。"""
        access = create_access_token(user.username, user.role, settings=self.settings)
        refresh = create_refresh_token(user.username, settings=self.settings)
        return AuthResult(
            access_token=access,
            refresh_token=refresh,
            expires_in=self.settings.access_token_minutes * 60,
            user=user,
        )

    async def _audit_and_commit(
        self,
        action: str,
        actor: str | None,
        detail: dict[str, object] | None,
        ip: str | None,
        request_id: str | None,
    ) -> None:
        """记录审计并立即提交，确保异常返回时审计不随事务回滚丢失。"""
        await self.audit.record(
            actor=actor, action=action, target=actor, detail=detail, ip=ip, request_id=request_id
        )
        await self.repos.session.commit()


__all__ = ["DEFAULT_ROLE", "AuthResult", "AuthService"]
