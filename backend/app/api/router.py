"""API 路由汇总。

挂载认证、管理员与只读业务子路由，并提供存活探针::

    api_router.include_router(auth.router)
    api_router.include_router(market.router)

WebSocket（``WS /ws``）不在本路由内，由 :func:`app.api.ws.attach_ws` 单独挂载。
"""

from fastapi import APIRouter

from app.api import (
    agent,
    auth,
    config,
    datasources,
    ingest,
    invitations,
    market,
    pages,
    reports,
    roles,
    users,
)
from app.core.config import get_settings

api_router = APIRouter(prefix="/api")

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(roles.router)
api_router.include_router(invitations.router)
api_router.include_router(market.router)
api_router.include_router(reports.router)
api_router.include_router(config.router)
api_router.include_router(datasources.router)
api_router.include_router(ingest.router)
api_router.include_router(pages.router)
api_router.include_router(agent.router)


@api_router.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """存活探针：进程在即 200。"""
    return {"status": "ok", "service": get_settings().app_name}


__all__ = ["api_router"]
