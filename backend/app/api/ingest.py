"""采集运维路由：任务明细/健康度读 + admin 手动触发。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_admin, require_page
from app.core.pages import PageKey
from app.models.auth import User
from app.repositories import Repositories, get_repositories
from app.schemas.config import (
    IngestHealthResponse,
    IngestJobsResponse,
    IngestTriggerOut,
    IngestTriggerRequest,
)
from app.services.ingest_service import IngestService

router = APIRouter(tags=["ingest"])

_require_quantconfig = require_page(PageKey.QUANTCONFIG)


def get_ingest_service(repos: Repositories = Depends(get_repositories)) -> IngestService:
    """请求级采集服务。"""
    return IngestService(repos)


@router.get("/ingest/jobs", response_model=IngestJobsResponse)
async def list_ingest_jobs(
    limit: int | None = Query(default=None, ge=1),
    _: User = Depends(_require_quantconfig),
    service: IngestService = Depends(get_ingest_service),
) -> IngestJobsResponse:
    """最近采集任务列表（条数受上限约束）。"""
    return await service.jobs(limit)


@router.get("/ingest/health", response_model=IngestHealthResponse)
async def get_ingest_health(
    _: User = Depends(_require_quantconfig),
    service: IngestService = Depends(get_ingest_service),
) -> IngestHealthResponse:
    """采集健康度汇总（各能力最近成功/失败与连续失败次数）。"""
    return await service.health()


@router.post("/ingest/trigger", response_model=IngestTriggerOut)
async def trigger_ingest(
    payload: IngestTriggerRequest,
    actor: User = Depends(require_admin),
    service: IngestService = Depends(get_ingest_service),
) -> IngestTriggerOut:
    """立即执行一个采集任务（admin；写审计、失效缓存并广播）。"""
    return await service.trigger(payload.task, payload.trade_date, actor=actor.username)


__all__ = ["get_ingest_service", "router"]
