"""性能测试夹具。

设计要点：

- **关闭全局限流**：spec 要求 1 万并发读，但 ``RateLimitMiddleware`` 默认 600/60s；
  通过 ``RATE_LIMIT_ENABLED=false`` 环境变量让中间件直接 pass-through（最早生效，
  必须在任何 ``app.main`` import 之前）。
- **依赖既有 ``tests/conftest.py`` 的 ``app`` / ``session_factory`` / ``make_user`` /
  ``login`` 等夹具**——这些由 pytest 自动从父 conftest 继承，无需显式 import。
- **注入 admin token + 缓存命中计数器**给压测用例。
"""

from __future__ import annotations

import os

# 必须在 import app.* 之前生效——``get_settings`` 用 ``lru_cache``，
# 加载中间件时 ``rate_limit_enabled`` 已经定型。
# 记录是否由本模块设置，供包级夹具在退出时还原（避免污染其他测试文件）。
_ENV_SET_BY_MODULE = "RATE_LIMIT_ENABLED" not in os.environ
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")


def _reset_settings_cache() -> None:
    """清掉 ``get_settings`` 的 lru_cache，让下一次访问重读环境变量。

    pytest 在加载父 conftest（``tests/conftest.py``）时已经触发了 ``app.main``
    模块顶层执行（``app = create_app()``），那时 ``get_settings()`` 被缓存为
    ``rate_limit_enabled=True``。此处清缓存后，本子包后续访问会重读 ``RATE_LIMIT_ENABLED``。
    """
    from app.core.config import get_settings as _get

    _get.cache_clear()


_reset_settings_cache()


from collections.abc import AsyncIterator, Iterator  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from app.core.cache import MemoryCache, get_cache  # noqa: E402
from app.services.cache_policy import reset_cache_policy  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from tests.perf.seed import read_endpoints, seed_perf_dataset  # noqa: E402


@pytest.fixture(autouse=True, scope="package")
def _restore_rate_limit_env() -> Iterator[None]:
    """本包用例全部跑完后还原 ``RATE_LIMIT_ENABLED``，避免污染其他测试文件。

    模块顶层的 ``os.environ.setdefault`` 是**进程级副作用**：若不还原，后续任何
    测试文件里 ``Settings()``（未显式传 ``rate_limit_enabled``）都会读到 ``false``，
    典型症状是 ``tests/test_security.py`` 的限流断言拿不到 429。仅当环境变量由本
    模块设置时才 pop，避免误删用户/CI 原本就设的值。
    """
    yield
    if _ENV_SET_BY_MODULE:
        os.environ.pop("RATE_LIMIT_ENABLED", None)
    _reset_settings_cache()


@pytest.fixture
async def perf_seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """种入性能测试所需数据集；返回 ``seed_perf_dataset`` 的元信息字典。"""
    return await seed_perf_dataset(session_factory)


@pytest.fixture
async def perf_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """``ASGITransport`` 异步客户端；无 loopback socket 开销。

    选择 ``ASGITransport`` 的理由（对比真实 uvicorn）：

    - spec 验收的「P95 ≤ 200ms」是应用层目标，**测量的是读路径服务逻辑 + DB + 缓存**，
      而不是 ASGI 服务器 vs TCP socket 的差异；
    - ``ASGITransport`` 在同一事件循环里调度，无需 ``ulimit`` / 端口分配，
      且天然隔离其他系统影响；
    - 真实多 worker + 跨进程 Redis 场景下，瓶颈会迁移到 PG 连接池，但本环境不可用，
      因此 ``ASGITransport`` 是当前唯一可重现的本地验证手段。
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as async_client:
        yield async_client


@pytest.fixture
async def perf_admin_token(
    make_user,
    login,
) -> str:
    """性能测试专用的 admin 令牌（独立用户名，避免与 ``admin_token`` 共享）。"""
    await make_user("perf-admin", role="admin")
    return await login("perf-admin")


@pytest.fixture
def cache_counter() -> Iterator[dict[str, int]]:
    """包装 ``MemoryCache.get``（在类级别），对 L1 + L2 全部 ``get`` 调用计数。

    选择在 ``MemoryCache`` 类级别 monkeypatch 是因为：

    - ``CachePolicy`` 每次 ``reset_cache_policy()`` 后重建，L1 是新实例；
    - L2 单例（``get_cache()``）跨测试持久；
    - 在类级别包装能让所有 ``MemoryCache`` 实例（无论新旧）都被计数。
    """
    if not isinstance(get_cache(), MemoryCache):
        pytest.skip("perf 测试要求 MemoryCache 后端")

    original_get = MemoryCache.get
    stats: dict[str, int] = {"hits": 0, "misses": 0}

    async def _counting_get(self: MemoryCache, key: str) -> Any:
        result = await original_get(self, key)
        if result is None:
            stats["misses"] += 1
        else:
            stats["hits"] += 1
        return result

    MemoryCache.get = _counting_get  # type: ignore[method-assign]
    try:
        yield stats
    finally:
        MemoryCache.get = original_get  # type: ignore[method-assign]
        reset_cache_policy()


__all__ = [
    "perf_admin_token",
    "perf_client",
    "perf_seed",
    "read_endpoints",
]
