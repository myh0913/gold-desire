"""认证与授权 DTO（Pydantic v2）。

安全约束：任何响应模型 SHALL NOT 暴露 ``password_hash``。:class:`UserOut` 仅声明
安全字段，故密码哈希在结构上不可能被序列化。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """登录请求。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    """注册请求：图形验证码必填，邀请码可选（决定角色）。"""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    captcha_id: str = Field(min_length=1, max_length=128)
    captcha: str = Field(min_length=1, max_length=16)
    invite_code: str | None = Field(default=None, max_length=64)


class CaptchaResponse(BaseModel):
    """图形验证码响应：``image`` 为 data URL，``captcha_id`` 用于注册回传。"""

    captcha_id: str
    image: str


class TokenResponse(BaseModel):
    """令牌响应：access token 走响应体，refresh 走 HttpOnly Cookie。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    """用户安全视图（不含 ``password_hash``）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    enabled: bool
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class MeResponse(BaseModel):
    """当前登录者信息：用户 + 角色 + 有效页面 key。"""

    user: UserOut
    role: str
    pages: list[str]


class UserCreate(BaseModel):
    """管理员创建用户。"""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="viewer", max_length=32)


class UserUpdate(BaseModel):
    """管理员更新用户：角色与启用状态二选一或同时。"""

    role: str | None = Field(default=None, max_length=32)
    enabled: bool | None = None


class PasswordReset(BaseModel):
    """管理员重置用户密码。"""

    password: str = Field(min_length=8, max_length=256)


class RoleCreate(BaseModel):
    """新建自定义角色。"""

    name: str = Field(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=64)


class RoleOut(BaseModel):
    """角色视图：含由注册表派生的页面权限与是否已自定义。"""

    name: str
    label: str
    is_builtin: bool
    pages: list[str]
    pages_configured: bool


class RolePagesUpdate(BaseModel):
    """更新角色页面权限：key 由服务端对照注册表校验，未知 key 一律拒绝。"""

    pages: list[str] = Field(default_factory=list)


class InvitationCreate(BaseModel):
    """创建邀请码：有效期 1~365 天。"""

    role: str = Field(default="viewer", max_length=32)
    expires_in_days: int = Field(default=7, ge=1, le=365)


class InvitationOut(BaseModel):
    """邀请码视图。"""

    model_config = ConfigDict(from_attributes=True)

    code: str
    role: str
    expires_at: datetime | None = None
    used_by: str | None = None
    used_at: datetime | None = None
    revoked: bool
    created_at: datetime | None = None


__all__ = [
    "CaptchaResponse",
    "InvitationCreate",
    "InvitationOut",
    "LoginRequest",
    "MeResponse",
    "PasswordReset",
    "RegisterRequest",
    "RoleCreate",
    "RoleOut",
    "RolePagesUpdate",
    "TokenResponse",
    "UserCreate",
    "UserOut",
    "UserUpdate",
]
