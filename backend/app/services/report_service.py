"""报告读服务 + 回测触发：建议报告、回测任务、回测执行。

读路径只访问 DB + 缓存；写路径（:meth:`ReportService.start_backtest`）为 admin 触发，
**异步执行**：接口只创建 ``status=running`` 的任务行并立即返回，重活交给后台任务
（独立会话，随 api 进程事件循环执行；完成/失败由后台任务更新状态并失效报告缓存）。
前端/Agent 经 ``GET /backtest/runs`` 轮询任务状态。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date
from typing import Any, cast

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.db.session import get_session_factory
from app.engine.backtest import resolve_factor_params, run_backtest
from app.engine.dragon_samples import build_samples
from app.factors.registry import FactorRepos
from app.repositories import Repositories
from app.repositories.reads import ReadRepository
from app.schemas.common import DatesResponse
from app.schemas.report import (
    AdviceLatestResponse,
    AdviceReportOut,
    AdviceResponse,
    BacktestRunOut,
    BacktestRunsResponse,
    DragonPoolItemOut,
    DragonPoolResponse,
)
from app.services.cache_policy import CachePolicy, get_cache_policy, query_key
from app.services.paging import clamp_limit
from app.strategies.protocol import StrategyRepos, resolve_params
from app.strategies.registry import get_strategy

__all__ = ["ReportService"]

logger = logging.getLogger(__name__)

#: 后台回测任务的强引用集（asyncio 只持弱引用，不持会被 GC 中途取消）。
_BACKTEST_TASKS: set[asyncio.Task[None]] = set()


def _iso(value: date | None) -> str | None:
    """日期转 ISO 串（供缓存键）。"""
    return value.isoformat() if value is not None else None


class ReportService:
    """建议报告与回测任务服务。

    Args:
        repos: 请求级仓储容器。
        policy: 缓存策略；缺省用进程内单例。
        session_factory: 后台回测任务的会话工厂（测试注入以复用请求级内存库）；
            缺省用全局工厂（生产 PG）。
    """

    def __init__(
        self,
        repos: Repositories,
        policy: CachePolicy | None = None,
        *,
        session_factory: Any | None = None,
    ) -> None:
        self._repos = repos
        self._read = ReadRepository(repos.session)
        self._policy = policy if policy is not None else get_cache_policy()
        self._session_factory = session_factory

    # ------------------------------------------------------------------ 建议

    async def advice(
        self, *, on_date: date | None, kind: str | None, strategy_id: str | None
    ) -> AdviceResponse:
        """取某交易日建议报告（``date`` 缺省用库中最近有报告的交易日）。"""
        key = query_key(
            "report",
            {"view": "advice", "date": _iso(on_date), "kind": kind, "strategy": strategy_id},
        )

        async def loader() -> AdviceResponse:
            target = on_date
            if target is None:
                available = await self._repos.advice_reports.list_dates(1)
                target = available[0] if available else None
            rows = (
                await self._repos.advice_reports.get_by_date(target, kind, strategy_id)
                if target
                else []
            )
            return AdviceResponse(
                trade_date=target,
                kind=kind,
                strategy_id=strategy_id,
                items=[AdviceReportOut.model_validate(row) for row in rows],
            )

        return await self._policy.get_or_load("report", key, loader, AdviceResponse.model_validate)

    async def advice_latest(self, on_date: date | None) -> AdviceLatestResponse:
        """取某交易日最近一次运行的建议报告。"""
        key = query_key("report", {"view": "advice_latest", "date": _iso(on_date)})

        async def loader() -> AdviceLatestResponse:
            target = on_date
            if target is None:
                available = await self._repos.advice_reports.list_dates(1)
                target = available[0] if available else None
            row = await self._repos.advice_reports.latest(target) if target else None
            return AdviceLatestResponse(
                trade_date=target,
                item=AdviceReportOut.model_validate(row) if row is not None else None,
            )

        return await self._policy.get_or_load(
            "report", key, loader, AdviceLatestResponse.model_validate
        )

    async def advice_dates(self, limit: int | None) -> DatesResponse:
        """有建议报告的交易日列表（去重倒序）。"""
        effective = clamp_limit(limit, default=30)
        key = query_key("report", {"view": "advice_dates", "limit": effective})

        async def loader() -> DatesResponse:
            dates = await self._repos.advice_reports.list_dates(effective)
            return DatesResponse(dates=dates, limit=effective)

        return await self._policy.get_or_load(
            "report", key, loader, DatesResponse.model_validate
        )

    # -------------------------------------------------------------- 盘后建池

    async def dragon_pool(self, *, on_date: date | None) -> DragonPoolResponse:
        """取某交易日盘后建池候选（``date`` 缺省用最近有候选的交易日）。"""
        key = query_key("report", {"view": "dragon_pool", "date": _iso(on_date)})

        async def loader() -> DragonPoolResponse:
            target = on_date
            if target is None:
                target = await self._repos.dragon_pool.latest_date()
            rows = (
                await self._repos.dragon_pool.get_by_date(target) if target is not None else []
            )
            return DragonPoolResponse(
                trade_date=target,
                strategy_id=rows[0].strategy_id if rows else None,
                items=[DragonPoolItemOut.model_validate(row) for row in rows],
            )

        return await self._policy.get_or_load(
            "report", key, loader, DragonPoolResponse.model_validate
        )

    async def dragon_pool_dates(self, limit: int | None) -> DatesResponse:
        """有盘后建池候选的交易日列表（去重倒序）。"""
        effective = clamp_limit(limit, default=30)
        key = query_key("report", {"view": "dragon_pool_dates", "limit": effective})

        async def loader() -> DatesResponse:
            dates = await self._repos.dragon_pool.list_dates(effective)
            return DatesResponse(dates=dates, limit=effective)

        return await self._policy.get_or_load(
            "report", key, loader, DatesResponse.model_validate
        )

    # ------------------------------------------------------------------ 回测

    async def backtest_runs(self, limit: int | None) -> BacktestRunsResponse:
        """最近回测任务列表。"""
        effective = clamp_limit(limit, default=20)
        key = query_key("report", {"view": "backtest_runs", "limit": effective})

        async def loader() -> BacktestRunsResponse:
            rows = await self._repos.backtest_runs.list_recent(effective)
            return BacktestRunsResponse(
                limit=effective, items=[BacktestRunOut.model_validate(row) for row in rows]
            )

        return await self._policy.get_or_load(
            "report", key, loader, BacktestRunsResponse.model_validate
        )

    async def backtest_run(self, run_id: str) -> BacktestRunOut:
        """取单个回测任务（不存在 404）。"""
        key = query_key("report", {"view": "backtest_run", "run_id": run_id})

        async def loader() -> BacktestRunOut:
            row = await self._repos.backtest_runs.get(run_id)
            if row is None:
                raise NotFoundError(f"回测任务不存在：{run_id}", detail={"run_id": run_id})
            return BacktestRunOut.model_validate(row)

        return await self._policy.get_or_load("report", key, loader, BacktestRunOut.model_validate)

    async def run_backtest(
        self,
        *,
        start: date,
        end: date,
        strategy_id: str = "dragon",
        params_override: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> BacktestRunOut:
        """创建回测任务并**后台异步执行**，立即返回 ``status=running`` 的任务行。

        - 幂等键为生成的 ``run_id``；后台任务使用**独立会话**（请求会话随响应关闭）；
        - 完成 / 失败由后台任务更新 ``backtest_runs`` 并失效报告缓存；
        - 前端 / Agent 经 ``GET /backtest/runs`` 或 ``GET /backtest/runs/{id}`` 轮询。

        Raises:
            NotFoundError: 策略未注册。
            ValidationError: 起止日期非法。
        """
        if start > end:
            raise ValidationError("回测起始日不得晚于结束日", code="invalid_range")
        try:
            strategy_cls = get_strategy(strategy_id)
        except Exception as exc:  # StrategyRegistryError
            raise NotFoundError(str(strategy_id), detail={"strategy_id": strategy_id}) from exc

        run_id = uuid.uuid4().hex
        # 注：插件协议 ``BaseStrategy`` 未声明 ``build_paths`` / ``factor_param_ids`` /
        # ``build_factor_params``（它们是 DragonStrategy 的扩展点）。此处按 ``Any`` 调用，
        # 协议缺口已在交付说明中报告，不改动 strategies 内部。
        strategy: Any = strategy_cls()
        await self._repos.backtest_runs.create(
            run_id, start, end, [strategy_id], dict(params_override or {}), created_by=created_by
        )
        await self._repos.audit_logs.record(
            actor=created_by,
            action="backtest_run",
            target=run_id,
            detail={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "strategy_id": strategy_id,
                "status": "running",
            },
        )
        await self._policy.invalidate("report")

        task = asyncio.create_task(
            self._execute_backtest(
                run_id=run_id,
                start=start,
                end=end,
                strategy_cls=strategy_cls,
                strategy=strategy,
                strategy_id=strategy_id,
                params_override=params_override,
            )
        )
        # 持有强引用防任务被 GC（asyncio 只存弱引用）。
        _BACKTEST_TASKS.add(task)
        task.add_done_callback(_BACKTEST_TASKS.discard)

        row = await self._repos.backtest_runs.get(run_id)
        if row is None:  # pragma: no cover - 创建后必然存在
            raise NotFoundError(f"回测任务不存在：{run_id}", detail={"run_id": run_id})
        return BacktestRunOut.model_validate(row)

    async def _execute_backtest(
        self,
        *,
        run_id: str,
        start: date,
        end: date,
        strategy_cls: Any,
        strategy: Any,
        strategy_id: str,
        params_override: dict[str, Any] | None,
    ) -> None:
        """后台执行回测（独立会话；成功/失败均更新任务行并失效缓存）。"""
        factory = self._session_factory or get_session_factory(get_settings())
        try:
            async with factory() as session:
                repos = Repositories.build(session)
                resolved = await resolve_params(strategy_cls, cast("StrategyRepos", repos))
                params = dict(resolved.params)
                if params_override:
                    params.update(params_override)
                resolved_factors = await resolve_factor_params(
                    strategy.factor_param_ids(), cast("FactorRepos", repos)
                )
                factor_params = strategy.build_factor_params(params, resolved_factors)
                samples = await build_samples(repos, start, end)
                await run_backtest(
                    samples,
                    params,
                    paths=strategy.build_paths(params),
                    factor_params=factor_params,
                    strategy_id=strategy_id,
                    repos=repos,
                    run_id=run_id,
                )
                await session.commit()
        except Exception as exc:
            logger.exception("backtest_background_failed", extra={"run_id": run_id})
            async with factory() as session:
                failure_repos = Repositories.build(session)
                await failure_repos.backtest_runs.update_status(
                    run_id, "failed", error=f"{type(exc).__name__}: {exc}"
                )
                await session.commit()
        await get_cache_policy().invalidate("report")
