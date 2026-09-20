"""密码与令牌原语。

- 密码：``argon2-cffi`` 哈希（:func:`hash_password` / :func:`verify_password` /
  :func:`needs_rehash`），绝不明文或弱哈希存储。
- 令牌：``pyjwt`` 签发的 JWT，区分 ``access`` / ``refresh`` **类型**，
  :func:`decode_token` 对无效/过期/类型不符一律抛 :class:`AuthError`，
  因此 refresh token 不可能被当作 access token 使用。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings, get_settings
from app.core.errors import AuthError, ValidationError

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"

MIN_PASSWORD_LENGTH = 8
"""密码最小长度。"""

_WEAK_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "passw0rd",
        "12345678",
        "123456789",
        "qwertyui",
        "qwerty123",
        "11111111",
        "admin123",
        "abc12345",
        "letmein1",
        "iloveyou",
    }
)


def _hasher(settings: Settings | None = None) -> PasswordHasher:
    """按配置构造 Argon2 哈希器。"""
    resolved = settings or get_settings()
    return PasswordHasher(time_cost=max(1, resolved.password_hash_rounds))


def hash_password(password: str, *, settings: Settings | None = None) -> str:
    """使用 Argon2 哈希密码，返回可入库的编码串。"""
    return _hasher(settings).hash(password)


def verify_password(password: str, password_hash: str, *, settings: Settings | None = None) -> bool:
    """恒定时间校验密码，匹配返回 ``True``，否则 ``False``（不抛异常）。"""
    try:
        return bool(_hasher(settings).verify(password_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str, *, settings: Settings | None = None) -> bool:
    """判断既有哈希是否需按当前参数重算。"""
    try:
        return bool(_hasher(settings).check_needs_rehash(password_hash))
    except InvalidHashError:
        return True


def validate_password_strength(password: str) -> None:
    """校验密码强度：最小长度、非纯数字/字母、非常见弱口令。

    Raises:
        ValidationError: 不满足强度要求。
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"密码长度至少 {MIN_PASSWORD_LENGTH} 位",
            code="weak_password",
            detail={"min_length": MIN_PASSWORD_LENGTH},
        )
    if password.lower() in _WEAK_PASSWORDS:
        raise ValidationError("密码过于常见，请更换", code="weak_password")
    has_letter = any(ch.isalpha() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    if not (has_letter and has_digit):
        raise ValidationError("密码需同时包含字母与数字", code="weak_password")


def _encode(
    subject: str,
    token_type: str,
    ttl: timedelta,
    *,
    role: str | None = None,
    extra: Mapping[str, Any] | None = None,
    settings: Settings | None = None,
) -> str:
    """签发指定类型与 TTL 的 JWT。"""
    resolved = settings or get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if role is not None:
        payload["role"] = role
    if extra:
        payload.update(dict(extra))
    return jwt.encode(payload, resolved.jwt_secret, algorithm=resolved.jwt_algorithm)


def create_access_token(
    subject: str,
    role: str,
    extra: Mapping[str, Any] | None = None,
    *,
    settings: Settings | None = None,
    expires_minutes: int | None = None,
) -> str:
    """签发短期 access token（含 ``role`` 声明）。"""
    resolved = settings or get_settings()
    minutes = resolved.access_token_minutes if expires_minutes is None else expires_minutes
    return _encode(
        subject,
        TOKEN_TYPE_ACCESS,
        timedelta(minutes=minutes),
        role=role,
        extra=extra,
        settings=resolved,
    )


def create_refresh_token(
    subject: str,
    *,
    settings: Settings | None = None,
    expires_days: int | None = None,
) -> str:
    """签发长期 refresh token（不携带角色，仅用于换取 access token）。"""
    resolved = settings or get_settings()
    days = resolved.refresh_token_days if expires_days is None else expires_days
    return _encode(subject, TOKEN_TYPE_REFRESH, timedelta(days=days), settings=resolved)


def decode_token(
    token: str,
    *,
    expected_type: str = TOKEN_TYPE_ACCESS,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """校验并解码 JWT，强制类型匹配。

    Raises:
        AuthError: 签名无效、已过期、缺少 subject 或类型与 ``expected_type`` 不符。
    """
    resolved = settings or get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            resolved.jwt_secret,
            algorithms=[resolved.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("凭证已过期，请重新登录", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("无效凭证", code="invalid_token") from exc

    if payload.get("type") != expected_type:
        raise AuthError("凭证类型不匹配", code="token_type_mismatch")
    if not payload.get("sub"):
        raise AuthError("无效凭证", code="invalid_token")
    return payload


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "TOKEN_TYPE_ACCESS",
    "TOKEN_TYPE_REFRESH",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "hash_password",
    "needs_rehash",
    "validate_password_strength",
    "verify_password",
]
