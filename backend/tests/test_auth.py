"""认证/授权/审计集成测试（内存 SQLite + memory 缓存）。

覆盖：验证码、注册、邀请码、登录锁定（可注入时钟）、刷新、/me、
401/403+审计、最后一个管理员保护、自我操作保护、页面权限往返与限流。
"""

from __future__ import annotations

import httpx
import pytest
from app.api.deps import require_page
from app.core.cache import MemoryCache, get_cache
from app.core.captcha import captcha_key
from app.core.errors import AuthError, PermissionDeniedError, RateLimitedError, ValidationError
from app.core.pages import PageKey, all_page_keys, register_page
from app.core.security import hash_password
from app.models.auth import AuditLog, User
from app.repositories import Repositories
from app.services.auth_service import AuthService
from app.services.role_service import RoleService
from app.services.user_service import UserService
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import TEST_PASSWORD, auth_header

# --------------------------------------------------------------------- 工具


class FakeClock:
    """可注入的单调时钟（用于登录锁定到期测试）。"""

    def __init__(self, start: float = 1_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


async def _fetch_captcha(client: httpx.AsyncClient) -> tuple[str, str]:
    """取验证码 id 与（测试用）从缓存读出的答案。"""
    response = await client.get("/api/captcha")
    assert response.status_code == 200
    captcha_id = response.json()["captcha_id"]
    answer = await get_cache().get(captcha_key(captcha_id))
    return captcha_id, str(answer)


async def _register(client: httpx.AsyncClient, username: str, **extra: str) -> httpx.Response:
    """带正确验证码注册。"""
    captcha_id, answer = await _fetch_captcha(client)
    payload = {
        "username": username,
        "password": TEST_PASSWORD,
        "captcha_id": captcha_id,
        "captcha": answer,
        **extra,
    }
    return await client.post("/api/auth/register", json=payload)


# ------------------------------------------------------------------ 验证码/注册


async def test_register_requires_captcha(client: httpx.AsyncClient) -> None:
    """缺少验证码字段 → 422。"""
    response = await client.post(
        "/api/auth/register", json={"username": "newuser", "password": TEST_PASSWORD}
    )
    assert response.status_code == 422


async def test_register_rejects_wrong_captcha(client: httpx.AsyncClient) -> None:
    """验证码错误 → 422 且错误码明确。"""
    captcha_id, _ = await _fetch_captcha(client)
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "newuser",
            "password": TEST_PASSWORD,
            "captcha_id": captcha_id,
            "captcha": "ZZZZ",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_captcha"


async def test_register_success_default_role(client: httpx.AsyncClient) -> None:
    """验证码正确即可注册，默认角色为 viewer。"""
    response = await _register(client, "newuser")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "viewer"
    assert body["enabled"] is True
    assert "password_hash" not in body


# -------------------------------------------------------------------- 邀请码


async def test_invitation_sets_role_and_is_single_use(
    client: httpx.AsyncClient,
    make_user,
    login,
) -> None:
    """邀请码决定角色，且只能使用一次。"""
    await make_user("boss", role="admin")
    token = await login("boss")
    created = await client.post(
        "/api/admin/invitations",
        json={"role": "analyst", "expires_in_days": 7},
        headers=auth_header(token),
    )
    assert created.status_code == 201, created.text
    code = created.json()["code"]

    first = await _register(client, "alice", invite_code=code)
    assert first.status_code == 201, first.text
    assert first.json()["role"] == "analyst"

    second = await _register(client, "bob", invite_code=code)
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "invitation_used"


async def test_invitation_expiry_bounds(client: httpx.AsyncClient, make_user, login) -> None:
    """有效期超出 1~365 天 → 422。"""
    await make_user("boss2", role="admin")
    token = await login("boss2")
    response = await client.post(
        "/api/admin/invitations",
        json={"role": "viewer", "expires_in_days": 400},
        headers=auth_header(token),
    )
    assert response.status_code == 422


# ------------------------------------------------------------- 登录锁定/刷新


async def test_login_lockout_threshold_and_expiry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一 IP 连续 5 次失败锁定 5 分钟，到期后可再次登录（注入时钟）。"""
    async with session_factory() as session:
        session.add(
            User(
                username="victim",
                password_hash=hash_password(TEST_PASSWORD),
                role="viewer",
                enabled=True,
            )
        )
        await session.commit()

        clock = FakeClock()
        service = AuthService(
            Repositories.build(session), cache=MemoryCache(), clock=clock
        )

        for _ in range(4):
            with pytest.raises(AuthError):
                await service.login("victim", "wrong", ip="10.0.0.1")

        with pytest.raises(RateLimitedError) as locked:
            await service.login("victim", "wrong", ip="10.0.0.1")
        assert locked.value.detail == {"retry_after": 300}

        # 锁定期间即使密码正确也拒绝
        with pytest.raises(RateLimitedError):
            await service.login("victim", TEST_PASSWORD, ip="10.0.0.1")

        clock.advance(301)
        result = await service.login("victim", TEST_PASSWORD, ip="10.0.0.1")
        assert result.access_token


async def test_login_audit_written(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """登录成功与失败均写审计日志。"""
    async with session_factory() as session:
        session.add(
            User(
                username="audited",
                password_hash=hash_password(TEST_PASSWORD),
                role="viewer",
                enabled=True,
            )
        )
        await session.commit()
        service = AuthService(Repositories.build(session), cache=MemoryCache())
        await service.login("audited", TEST_PASSWORD, ip="1.2.3.4")
        with pytest.raises(AuthError):
            await service.login("audited", "nope", ip="1.2.3.4")
        actions = {
            row.action
            for row in (
                await session.execute(
                    select(AuditLog).where(AuditLog.actor == "audited")
                )
            )
            .scalars()
            .all()
        }
    assert {"login", "login_failed"} <= actions


async def test_refresh_flow(client: httpx.AsyncClient, make_user) -> None:
    """refresh cookie 可换取新 access token，并可用于 /me。"""
    await make_user("refresher")
    login_resp = await client.post(
        "/api/auth/login", json={"username": "refresher", "password": TEST_PASSWORD}
    )
    assert login_resp.status_code == 200
    assert any(cookie.name == "gd_refresh" for cookie in client.cookies.jar)
    set_cookie = login_resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/api/auth" in set_cookie

    refreshed = await client.post("/api/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    token = refreshed.json()["access_token"]

    me = await client.get("/api/auth/me", headers=auth_header(token))
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "refresher"


async def test_refresh_without_cookie_returns_401(client: httpx.AsyncClient) -> None:
    """无 refresh cookie → 401。"""
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


# --------------------------------------------------------------------- /me


async def test_me_shape_and_effective_pages(client: httpx.AsyncClient, make_user, login) -> None:
    """/me 返回 user + role + 由注册表派生的有效页面 key。"""
    await make_user("viewer1", role="viewer")
    token = await login("viewer1")
    response = await client.get("/api/auth/me", headers=auth_header(token))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"user", "role", "pages"}
    assert body["role"] == "viewer"
    assert PageKey.REVIEW.value in body["pages"]
    assert PageKey.SETTINGS.value not in body["pages"]
    assert "password_hash" not in response.text


# ------------------------------------------------------------ 401 / 403 + 审计


async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    """未认证访问受保护接口一律 401。"""
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/admin/users")).status_code == 401


async def test_viewer_forbidden_on_admin_route_writes_audit(
    client: httpx.AsyncClient,
    make_user,
    login,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """viewer 访问 admin 路由 → 403，且写入审计日志。"""
    await make_user("peek", role="viewer")
    token = await login("peek")
    response = await client.get("/api/admin/users", headers=auth_header(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] in {"admin_required", "page_forbidden"}

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "permission_denied", AuditLog.actor == "peek"
                )
            )
        ).scalars().all()
    assert rows


async def test_require_page_dependency_denies_and_allows(
    app,
    client: httpx.AsyncClient,
    make_user,
    login,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """页面级依赖：无该页面权限 → 403 + 审计；授予后放行。"""

    @app.get("/api/_test/review")
    async def _review(_: User = Depends(require_page(PageKey.REVIEW))) -> dict[str, bool]:
        return {"ok": True}

    await make_user("analyst1", role="analyst")

    async with session_factory() as session:
        await RoleService(Repositories.build(session)).set_pages(
            "ops", "analyst", [PageKey.OVERVIEW.value]
        )
        await session.commit()

    token = await login("analyst1")
    denied = await client.get("/api/_test/review", headers=auth_header(token))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "page_forbidden"

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "permission_denied",
                    AuditLog.target == PageKey.REVIEW.value,
                )
            )
        ).scalars().all()
        assert rows
        await RoleService(Repositories.build(session)).set_pages(
            "ops", "analyst", [PageKey.OVERVIEW.value, PageKey.REVIEW.value]
        )
        await session.commit()

    allowed = await client.get("/api/_test/review", headers=auth_header(token))
    assert allowed.status_code == 200


# ------------------------------------------------------- 最后管理员 / 自我保护


async def test_last_enabled_admin_protected_all_paths(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """最后一个启用管理员不可被降级 / 停用 / 删除。"""
    async with session_factory() as session:
        await RoleService(Repositories.build(session)).ensure_builtin_roles()
        session.add(
            User(
                username="solo",
                password_hash=hash_password(TEST_PASSWORD),
                role="admin",
                enabled=True,
            )
        )
        await session.commit()
        service = UserService(Repositories.build(session))

        with pytest.raises(PermissionDeniedError):
            await service.update_role("ops", "solo", "viewer")
        with pytest.raises(PermissionDeniedError):
            await service.set_enabled("ops", "solo", False)
        with pytest.raises(PermissionDeniedError):
            await service.delete_user("ops", "solo")


async def test_self_operations_forbidden(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """任何用户不可对自身降级 / 停用 / 删除。"""
    async with session_factory() as session:
        await RoleService(Repositories.build(session)).ensure_builtin_roles()
        session.add(
            User(
                username="root",
                password_hash=hash_password(TEST_PASSWORD),
                role="admin",
                enabled=True,
            )
        )
        await session.commit()
        service = UserService(Repositories.build(session))

        with pytest.raises(PermissionDeniedError):
            await service.update_role("root", "root", "viewer")
        with pytest.raises(PermissionDeniedError):
            await service.set_enabled("root", "root", False)
        with pytest.raises(PermissionDeniedError):
            await service.delete_user("root", "root")


async def test_second_admin_can_be_demoted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """存在第二个启用管理员时，允许降级其中之一。"""
    async with session_factory() as session:
        await RoleService(Repositories.build(session)).ensure_builtin_roles()
        session.add_all(
            [
                User(
                    username="a1",
                    password_hash=hash_password(TEST_PASSWORD),
                    role="admin",
                    enabled=True,
                ),
                User(
                    username="a2",
                    password_hash=hash_password(TEST_PASSWORD),
                    role="admin",
                    enabled=True,
                ),
            ]
        )
        await session.commit()
        service = UserService(Repositories.build(session))
        updated = await service.update_role("a1", "a2", "viewer")
        assert updated.role == "viewer"


# ------------------------------------------------------- 页面权限注册表往返


async def test_role_pages_round_trip_including_new_page(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """保存角色页面 → 回读集合相等；新增页面后矩阵自动包含，绝不静默丢弃。"""
    async with session_factory() as session:
        service = RoleService(Repositories.build(session))
        await service.ensure_builtin_roles()

        saved = [PageKey.OVERVIEW.value, PageKey.REVIEW.value]
        await service.set_pages("ops", "viewer", saved)
        assert set(await service.get_pages("viewer")) == set(saved)

        # 新增一个页面 key（模拟日后上线新页面）
        register_page("alpha", "新页面")
        matrix = await service.page_matrix("viewer")
        assert "alpha" in matrix  # 矩阵行集恒等于注册表 → 新页面出现

        # 未配置的角色默认可见全部非管理员页面（含新页面）
        analyst_pages = await service.get_pages("analyst")
        assert "alpha" in analyst_pages

        # 管理员恒为全部页面
        assert set(await service.get_pages("admin")) == set(all_page_keys())


async def test_set_pages_rejects_unknown_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """未知页面 key 一律拒绝（不静默丢弃）。"""
    async with session_factory() as session:
        service = RoleService(Repositories.build(session))
        await service.ensure_builtin_roles()
        with pytest.raises(ValidationError):
            await service.set_pages("ops", "viewer", ["overview", "bogus"])


async def test_api_rejects_unknown_page_key(
    client: httpx.AsyncClient, make_user, login
) -> None:
    """API 层同样拒绝未知页面 key。"""
    await make_user("boss3", role="admin")
    token = await login("boss3")
    response = await client.put(
        "/api/admin/roles/viewer/pages",
        json={"pages": ["overview", "bogus"]},
        headers=auth_header(token),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_page_key"


async def test_admin_role_pages_update_round_trip(
    client: httpx.AsyncClient, make_user, login
) -> None:
    """通过 API 保存并回读角色页面权限。"""
    await make_user("boss4", role="admin")
    token = await login("boss4")
    saved = [PageKey.OVERVIEW.value, PageKey.POOLS.value, PageKey.REVIEW.value]
    response = await client.put(
        "/api/admin/roles/viewer/pages",
        json={"pages": saved},
        headers=auth_header(token),
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["pages"]) == set(saved)
    assert response.json()["pages_configured"] is True
