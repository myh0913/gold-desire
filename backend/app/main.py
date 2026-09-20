"""FastAPI 应用装配入口。

``create_app()`` 为工厂函数：装配中间件、异常处理器、路由与生命周期，
**导入与装配过程不要求 DB/Redis 可用**（引擎与缓存均惰性初始化）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.api.ws import attach_ws
from app.core.cache import RedisCache, close_cache, get_cache
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import register_middlewares
from app.db.session import close_engine, get_session_factory, ping_database
from app.factors.registry import sync_definitions as sync_factor_definitions
from app.repositories import Repositories
from app.strategies import discover_plugins
from app.strategies import sync_definitions as sync_strategy_definitions

if TYPE_CHECKING:
    from app.factors.registry import FactorRepos
    from app.strategies.protocol import StrategyRepos

logger = logging.getLogger(__name__)


async def _sync_registry_definitions() -> dict[str, list[str]]:
    """严格发现策略插件，并把策略/因子定义幂等同步进 DB（DB 始终反映代码）。

    见 ``app/strategies/__init__.py`` 尾注：导入期的发现是**非严格**的（坏插件告警
    继续），启动路径应再以 ``strict=True`` 跑一次。同步 ``strategy_defs`` /
    ``factor_defs`` 后，策略列表接口才有数据，且 ``enabled`` 状态可跨重启持久
    （``sync_definitions`` 对已存在行保留原 ``enabled``）。
    """
    result = discover_plugins(strict=True)
    if result.errors:
        logger.warning("strategy_plugin_errors", extra={"errors": [str(e) for e in result.errors]})
    settings = get_settings()
    factory = get_session_factory(settings)
    async with factory() as session:
        repos = Repositories.build(session)
        # ``Repositories`` 与两个最小协议结构兼容但类名不同，mypy 需显式收窄
        # （与 ``config_service._strategy_out`` 内的 cast 同款处理）。
        synced_strategies = await sync_strategy_definitions(
            cast("StrategyRepos", repos)
        )
        synced_factors = await sync_factor_definitions(cast("FactorRepos", repos))
        await session.commit()
        return {"strategies": synced_strategies, "factors": synced_factors}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时配置日志与缓存，关闭时释放资源。"""
    settings = get_settings()
    configure_logging()
    logger.info(
        "app_starting",
        extra={
            "app": settings.app_name,
            "env": settings.app_env,
            "cache_backend": settings.cache_backend,
            "database": settings.database_url.split("://", 1)[0],
        },
    )
    get_cache()
    # 定义同步：DB 反映代码注册表（失败不阻止启动——/ready 会暴露 DB 故障，
    # 且 config 读接口有代码注册表/缓存兜底；启动日志会记录失败原因）。
    try:
        synced = await _sync_registry_definitions()
        logger.info(
            "registry_definitions_synced",
            extra={
                "strategies": synced["strategies"],
                "factors": synced["factors"],
            },
        )
    except Exception:
        logger.exception("registry_definitions_sync_failed")
    yield
    await close_cache()
    await close_engine()
    logger.info("app_stopped", extra={"app": settings.app_name})


def create_app() -> FastAPI:
    """构建并返回 FastAPI 应用实例。"""
    settings = get_settings()
    configure_logging()

    app = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.app_version,
        description="gold-desire 量化平台后端：只读服务侧 + 采集侧分离。",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    register_middlewares(app, settings)
    register_exception_handlers(app)
    app.include_router(api_router)
    attach_ws(app)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """存活探针：进程存活即 200，不检查任何外部依赖。"""
        return {"status": "ok", "service": settings.app_name, "version": settings.app_version}

    @app.get("/ready", tags=["system"])
    async def ready(response: Response) -> JSONResponse:
        """就绪探针：检查数据库与缓存后端连通性。"""
        checks: dict[str, Any] = {}

        db_ok = await ping_database(settings)
        checks["database"] = {"ok": db_ok, "backend": settings.database_url.split("://", 1)[0]}

        cache = get_cache()
        if isinstance(cache, RedisCache):
            cache_ok = await cache.ping()
            checks["cache"] = {"ok": cache_ok, "backend": "redis"}
        else:
            checks["cache"] = {"ok": True, "backend": "memory"}

        all_ok = all(bool(item["ok"]) for item in checks.values())
        status_code = 200 if all_ok else 503
        response.status_code = status_code
        payload: dict[str, Any] = {
            "status": "ready" if all_ok else "not_ready",
            "checks": checks,
        }
        return JSONResponse(status_code=status_code, content=payload)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=_settings.server_host,
        port=_settings.server_port,
        reload=_settings.app_debug,
    )
