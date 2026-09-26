"""报告路由：建议报告（读）、复盘（读）与回测（读 + admin 异步触发）。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import client_ip, require_admin, require_page
from app.core.logging import get_request_id
from app.core.pages import PageKey
from app.models.auth import User
from app.repositories import Repositories, get_repositories
from app.schemas.common import DatesResponse
from app.schemas.report import (
    AdviceLatestResponse,
    AdviceMarkOut,
    AdviceMarkRequest,
    AdviceMarksResponse,
    AdviceResponse,
    BacktestRunOut,
    BacktestRunRequest,
    BacktestRunsResponse,
    DragonPoolResponse,
)
from app.schemas.review import ReviewResponse, ReviewStrategyStatsResponse
from app.services.report_service import ReportService
from app.services.review_service import ReviewService

router = APIRouter(tags=["reports"])


def get_report_service(
    request: Request, repos: Repositories = Depends(get_repositories)
) -> ReportService:
    """请求级报告服务（携带应用级会话工厂，供后台回测任务复用同一库）。"""
    return ReportService(
        repos, session_factory=getattr(request.app.state, "session_factory", None)
    )


def get_review_service(repos: Repositories = Depends(get_repositories)) -> ReviewService:
    """请求级复盘服务。"""
    return ReviewService(repos)


@router.get("/advice", response_model=AdviceResponse)
async def get_advice(
    date_: date | None = Query(default=None, alias="date"),
    kind: str | None = Query(default=None, max_length=32),
    strategy_id: str | None = Query(default=None, max_length=64),
    _: User = Depends(require_page(PageKey.ADVICE)),
    service: ReportService = Depends(get_report_service),
) -> AdviceResponse:
    """取某交易日建议报告（缺省取最近有报告的交易日）。"""
    return await service.advice(on_date=date_, kind=kind, strategy_id=strategy_id)


@router.get("/advice/latest", response_model=AdviceLatestResponse)
async def get_advice_latest(
    date_: date | None = Query(default=None, alias="date"),
    _: User = Depends(require_page(PageKey.ADVICE)),
    service: ReportService = Depends(get_report_service),
) -> AdviceLatestResponse:
    """取某交易日最近一次运行的建议报告。"""
    return await service.advice_latest(date_)


@router.get("/advice/dates", response_model=DatesResponse)
async def get_advice_dates(
    limit: int | None = Query(default=None, ge=1),
    _: User = Depends(require_page(PageKey.ADVICE)),
    service: ReportService = Depends(get_report_service),
) -> DatesResponse:
    """有建议报告的交易日列表（去重倒序）。"""
    return await service.advice_dates(limit)


@router.get("/advice/marks", response_model=AdviceMarksResponse)
async def list_advice_marks(
    date_: date = Query(alias="date"),
    _: User = Depends(require_page(PageKey.ADVICE)),
    repos: Repositories = Depends(get_repositories),
) -> AdviceMarksResponse:
    """某交易日「已买入」标记列表。"""
    rows = await repos.advice_marks.get_by_date(date_)
    return AdviceMarksResponse(
        trade_date=date_, items=[AdviceMarkOut.model_validate(row) for row in rows]
    )


@router.post("/advice/marks", response_model=AdviceMarksResponse)
async def mark_advice_bought(
    payload: AdviceMarkRequest,
    request: Request,
    actor: User = Depends(require_page(PageKey.ADVICE)),
    repos: Repositories = Depends(get_repositories),
) -> AdviceMarksResponse:
    """标记/取消「已买入」（取消 = 删行；写操作全量审计）。"""
    await repos.advice_marks.set_bought(
        payload.trade_date,
        payload.strategy_id,
        payload.code,
        payload.bought,
        marked_by=actor.username,
    )
    await repos.audit_logs.record(
        actor=actor.username,
        action="advice_mark",
        target=f"{payload.trade_date}:{payload.strategy_id}:{payload.code}",
        detail={"bought": payload.bought},
        ip=client_ip(request),
        request_id=get_request_id(),
    )
    rows = await repos.advice_marks.get_by_date(payload.trade_date)
    return AdviceMarksResponse(
        trade_date=payload.trade_date, items=[AdviceMarkOut.model_validate(row) for row in rows]
    )


@router.get("/dragon/pool", response_model=DragonPoolResponse)
async def get_dragon_pool(
    date_: date | None = Query(default=None, alias="date"),
    _: User = Depends(require_page(PageKey.ADVICE)),
    service: ReportService = Depends(get_report_service),
) -> DragonPoolResponse:
    """取某交易日盘后建池候选（缺省取最近有候选的交易日）。"""
    return await service.dragon_pool(on_date=date_)


@router.get("/dragon/pool/dates", response_model=DatesResponse)
async def get_dragon_pool_dates(
    limit: int | None = Query(default=None, ge=1),
    _: User = Depends(require_page(PageKey.ADVICE)),
    service: ReportService = Depends(get_report_service),
) -> DatesResponse:
    """有盘后建池候选的交易日列表（去重倒序）。"""
    return await service.dragon_pool_dates(limit)


@router.get("/review", response_model=ReviewResponse)
async def get_review(
    date_: date | None = Query(default=None, alias="date", description="交易日；缺省取最新"),
    _: User = Depends(require_page(PageKey.REVIEW)),
    service: ReviewService = Depends(get_review_service),
) -> ReviewResponse:
    """取某交易日复盘聚合（情绪 / 池型 / 天梯 / 建议回溯）。"""
    return await service.review(date_)


@router.get("/review/dates", response_model=DatesResponse)
async def get_review_dates(
    limit: int | None = Query(default=None, ge=1),
    _: User = Depends(require_page(PageKey.REVIEW)),
    service: ReviewService = Depends(get_review_service),
) -> DatesResponse:
    """可复盘交易日列表（去重倒序）。"""
    return await service.review_dates(limit)


@router.get("/review/strategy-stats", response_model=ReviewStrategyStatsResponse)
async def get_review_strategy_stats(
    days: int | None = Query(
        default=30, ge=0, le=730, description="自然日窗口，锚定最近有建议的交易日；0 表示全部"
    ),
    _: User = Depends(require_page(PageKey.REVIEW)),
    service: ReviewService = Depends(get_review_service),
) -> ReviewStrategyStatsResponse:
    """跨日分策略战绩聚合（样本 / 胜率 / 平均收益）。"""
    return await service.strategy_stats(days)


@router.get("/backtest/runs", response_model=BacktestRunsResponse)
async def list_backtest_runs(
    limit: int | None = Query(default=None, ge=1),
    _: User = Depends(require_page(PageKey.BACKTEST)),
    service: ReportService = Depends(get_report_service),
) -> BacktestRunsResponse:
    """最近回测任务列表（条数受上限约束）。"""
    return await service.backtest_runs(limit)


@router.get("/backtest/runs/{run_id}", response_model=BacktestRunOut)
async def get_backtest_run(
    run_id: str,
    _: User = Depends(require_page(PageKey.BACKTEST)),
    service: ReportService = Depends(get_report_service),
) -> BacktestRunOut:
    """取单个回测任务（含 A/B/C 三段报告）。"""
    return await service.backtest_run(run_id)


@router.post("/backtest/run", response_model=BacktestRunOut)
async def trigger_backtest(
    payload: BacktestRunRequest,
    actor: User = Depends(require_admin),
    service: ReportService = Depends(get_report_service),
) -> BacktestRunOut:
    """触发一次回测（**异步执行**：立即返回 ``status=running`` 的任务行，
    完成/失败由后台任务更新，经 ``GET /backtest/runs`` 轮询）。"""
    return await service.run_backtest(
        start=payload.start,
        end=payload.end,
        strategy_id=payload.strategy_id,
        params_override=payload.params,
        created_by=actor.username,
    )


__all__ = ["get_report_service", "router"]
