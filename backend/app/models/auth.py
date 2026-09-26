"""认证与授权模型：用户、角色、页面权限、邀请码与审计日志。

对应 spec「认证、授权与安全防护」：RBAC 三角色起步、页面/接口双层校验、
邀请码决定角色、全量写操作审计。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JsonType, TimestampMixin


class User(TimestampMixin, Base):
    """系统用户。

    - ``password_hash`` 仅存 Argon2 哈希，SHALL NOT 存明文或弱哈希。
    - ``role`` 引用 :class:`Role` 的角色名；页面/接口权限由角色决定。
    - ``last_login_at`` 为最近一次成功登录时间，供安全审计使用。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False, default="viewer", doc="角色名"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(), doc="是否启用"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="最近登录时间"
    )
    capital_yuan: Mapped[float | None] = mapped_column(
        Numeric(14, 2), nullable=True, doc="账户本金（元），用户自助维护，用于建议仓位折算股数"
    )


class Role(Base):
    """角色定义（``admin`` / ``analyst`` / ``viewer`` 为内置角色）。"""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(32), primary_key=True, doc="角色名（业务主键）")
    label: Mapped[str] = mapped_column(String(64), nullable=False, doc="显示名")
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), doc="是否内置角色"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RolePage(Base):
    """角色 → 页面 key 的权限矩阵（前端隐藏仅为体验，服务端为准）。"""

    __tablename__ = "role_pages"
    __table_args__ = (UniqueConstraint("role", "page_key", name="uq_role_pages_role_page"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("roles.name", ondelete="CASCADE"),
        index=True,
        nullable=False,
        doc="角色名",
    )
    page_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False, doc="页面 key")


class Invitation(Base):
    """邀请码：注册时凭码决定角色，支持有效期与吊销。"""

    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="viewer", doc="使用后授予的角色名"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="过期时间，空表示不过期"
    )
    used_by: Mapped[str | None] = mapped_column(String(64), nullable=True, doc="使用者用户名")
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="使用时间"
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), doc="是否已吊销"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    """审计日志：写操作、登录/登出、权限变更、Agent 工具调用全量留痕。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, server_default=func.now(), nullable=False
    )
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True, doc="操作者用户名")
    action: Mapped[str] = mapped_column(String(64), nullable=False, doc="动作标识")
    target: Mapped[str | None] = mapped_column(String(128), nullable=True, doc="操作对象标识")
    detail: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True, doc="结构化明细")
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True, doc="客户端 IP（含 IPv6）")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, doc="请求串联 ID")


__all__ = ["AuditLog", "Invitation", "Role", "RolePage", "User"]
