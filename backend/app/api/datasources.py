"""数据源运维路由：注册表读 + admin 主备/探测/启停写接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_admin, require_page
from app.core.pages import PageKey
from app.models.auth import User
from app.repositories import Repositories, get_repositories
from app.schemas.config import (
    DatasourcePingRequest,
    DatasourcePingResponse,
    DatasourcePrefsResponse,
    DatasourcePrefsUpdate,
    DatasourcesResponse,
    DatasourceStateOut,
)
from app.services.datasource_service import DatasourceService

router = APIRouter(tags=["datasources"])

_require_quantconfig = require_page(PageKey.QUANTCONFIG)


def get_datasource_service(repos: Repositories = Depends(get_repositories)) -> DatasourceService:
    """请求级数据源服务。"""
    return DatasourceService(repos)


@router.get("/datasources", response_model=DatasourcesResponse)
async def list_datasources(
    _: User = Depends(_require_quantconfig),
    service: DatasourceService = Depends(get_datasource_service),
) -> DatasourcesResponse:
    """数据源注册表 + 能力取数顺序 + 最新健康度。"""
    return await service.datasources()


@router.put("/datasources/prefs", response_model=DatasourcePrefsResponse)
async def save_datasource_prefs(
    payload: DatasourcePrefsUpdate,
    actor: User = Depends(require_admin),
    service: DatasourceService = Depends(get_datasource_service),
) -> DatasourcePrefsResponse:
    """保存能力 → 有序源列表（主备切换），下一轮采集即热生效。"""
    return await service.set_prefs(payload.prefs, actor=actor.username)


@router.post("/datasources/ping", response_model=DatasourcePingResponse)
async def ping_datasources(
    payload: DatasourcePingRequest,
    actor: User = Depends(require_admin),
    service: DatasourceService = Depends(get_datasource_service),
) -> DatasourcePingResponse:
    """连通性探测（缺省探测全部已注册源），结果写入健康度表。"""
    return await service.ping(payload.sources, actor=actor.username)


@router.post("/datasources/{source_id}/enable", response_model=DatasourceStateOut)
async def enable_datasource(
    source_id: str,
    actor: User = Depends(require_admin),
    service: DatasourceService = Depends(get_datasource_service),
) -> DatasourceStateOut:
    """启用数据源。"""
    return await service.set_enabled(source_id, True, actor=actor.username)


@router.post("/datasources/{source_id}/disable", response_model=DatasourceStateOut)
async def disable_datasource(
    source_id: str,
    actor: User = Depends(require_admin),
    service: DatasourceService = Depends(get_datasource_service),
) -> DatasourceStateOut:
    """停用数据源。"""
    return await service.set_enabled(source_id, False, actor=actor.username)


__all__ = ["get_datasource_service", "router"]
