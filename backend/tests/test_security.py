"""安全原语测试：密码哈希、JWT 类型/过期、限流器独立性、响应模型与中间件。

无需数据库与 Redis：全部走内存实现。
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.core.cache import MemoryCache
from app.core.config import Settings
from app.core.errors import AuthError, ValidationError
from app.core.rate_limit import RateLimiter, RateLimitMiddleware
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.models.auth import User
from app.schemas.auth import UserOut
from fastapi import FastAPI


def test_password_hash_verify_and_rehash() -> None:
    """Argon2 哈希：非明文、可校验、参数变化时需重算。"""
    settings = Settings(password_hash_rounds=2)
    digest = hash_password("Passw0rd1", settings=settings)
    assert digest != "Passw0rd1"
    assert digest.startswith("$argon2")
    assert verify_password("Passw0rd1", digest, settings=settings) is True
    assert verify_password("wrong-password", digest, settings=settings) is False
    assert needs_rehash(digest, settings=settings) is False
    assert needs_rehash(digest, settings=Settings(password_hash_rounds=5)) is True


@pytest.mark.parametrize("weak", ["short", "12345678", "password", "abcdefgh"])
def test_weak_password_rejected(weak: str) -> None:
    """弱口令（过短/纯数字/纯字母/常见）应被拒绝。"""
    with pytest.raises(ValidationError):
        validate_password_strength(weak)


def test_strong_password_accepted() -> None:
    """长度足够且含字母与数字的密码通过。"""
    validate_password_strength("Str0ngPass")


def test_token_type_confusion_rejected() -> None:
    """refresh token 不能当 access 用，反之亦然。"""
    settings = Settings()
    access = create_access_token("u", "viewer", settings=settings)
    refresh = create_refresh_token("u", settings=settings)

    assert decode_token(access, expected_type="access", settings=settings)["sub"] == "u"
    with pytest.raises(AuthError):
        decode_token(refresh, expected_type="access", settings=settings)
    with pytest.raises(AuthError):
        decode_token(access, expected_type="refresh", settings=settings)


def test_expired_token_rejected() -> None:
    """已过期的 access token 应被拒绝。"""
    settings = Settings()
    token = create_access_token("u", "viewer", settings=settings, expires_minutes=-1)
    with pytest.raises(AuthError):
        decode_token(token, settings=settings)


def test_invalid_token_rejected() -> None:
    """非法字符串应被拒绝。"""
    with pytest.raises(AuthError):
        decode_token("not-a-jwt", settings=Settings())


def test_user_out_never_exposes_password_hash() -> None:
    """UserOut 结构上不含 password_hash。"""
    user = User(id=1, username="u", password_hash="argon2id$secret", role="viewer", enabled=True)
    payload = UserOut.model_validate(user).model_dump()
    assert "password_hash" not in payload
    assert "argon2id$secret" not in json.dumps(payload)


async def test_rate_limiter_buckets_independent() -> None:
    """不同命名桶互不干扰；同桶不同 key 也互不影响。"""
    cache = MemoryCache()
    global_limiter = RateLimiter(cache, "global", 2, 60)
    login_limiter = RateLimiter(cache, "login", 2, 60)

    assert (await global_limiter.hit("1.1.1.1")).allowed is True
    assert (await global_limiter.hit("1.1.1.1")).allowed is True
    assert (await global_limiter.hit("1.1.1.1")).allowed is False
    # 登录桶不受全局桶影响
    assert (await login_limiter.hit("1.1.1.1")).allowed is True
    # 同桶不同 key 独立
    assert (await global_limiter.hit("2.2.2.2")).allowed is True


async def test_global_limiter_exempts_health() -> None:
    """全局限流中间件对 /api/* 生效，但豁免 /health 探针。"""
    inner = FastAPI()

    @inner.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @inner.get("/api/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    inner.add_middleware(
        RateLimitMiddleware,
        settings=Settings(rate_limit_requests=1, rate_limit_window_seconds=60),
        cache=MemoryCache(),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=inner), base_url="http://testserver"
    ) as client:
        for _ in range(5):
            assert (await client.get("/health")).status_code == 200
        assert (await client.get("/api/ping")).status_code == 200
        assert (await client.get("/api/ping")).status_code == 429
