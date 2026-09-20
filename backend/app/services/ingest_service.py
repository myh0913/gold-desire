"""采集运维服务：任务明细读、健康度读、手动触发。

读方法只访问 DB + 缓存；触发为 admin 操作，写审计、失效缓存并向 WS 广播
（``broadcast`` 失败不影响接口）。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.ingest.api_hooks import ingest_health, trigger_task
from app.repositories import Repositories
from app.schemas.config import (
    CapabilityHealthOut,
    IngestHealthResponse,
    IngestJobOut,
    IngestJobsResponse,
    IngestTriggerOut,
)
from app.services.cache_policy import CachePolicy, get_cache_policy, query_key
from app.services.paging import clamp_limit

__all__ = ["IngestService"]

logger = logging.getLogger(__name__)

#: 健康度统计回看窗口（自然日）。
HEALTH_WINDOW_DAYS = 7


class IngestService:
    """采集任务与健康度服务。"""

    def __init__(self, repos: Repositories, policy: CachePolicy | None = None) -> None:
        self._repos = repos
        self._policy = policy if policy is not None else get_cache_policy()

    async def jobs(self, limit: int | None) -> IngestJobsResponse:
        """最近采集任务列表（条数受 ``limit`` 约束）。"""
        effective = clamp_limit(limit, default=50)
        key = query_key("ingest", {"view": "jobs", "limit": effective})

        async def loader() -> IngestJobsResponse:
            rows = await self._repos.ingest_jobs.list_recent(effective)
            return IngestJobsResponse(
                limit=effective, items=[IngestJobOut.model_validate(row) for row in rows]
            )

        return await self._policy.get_or_load(
            "ingest", key, loader, IngestJobsResponse.model_validate
        )

    async def health(self) -> IngestHealthResponse:
        """采集健康度汇总（各能力最近成功/失败与连续失败次数）。"""
        key = query_key("ingest", {"view": "health"})

        async def loader() -> IngestHealthResponse:
            since = datetime.now(UTC) - timedelta(days=HEALTH_WINDOW_DAYS)
            payload = await ingest_health(self._repos, since)
            capabilities = {
                name: CapabilityHealthOut(
                    last_success=item.get("last_success"),
                    last_failure=item.get("last_failure"),
                    consecutive_failures=int(item.get("consecutive_failures", 0)),
                )
                for name, item in payload["capabilities"].items()
            }
            return IngestHealthResponse(since=since, capabilities=capabilities)

        return await self._policy.get_or_load(
            "ingest", key, loader, IngestHealthResponse.model_validate
        )

    async def trigger(
        self, task: str, on_date: date | None, *, actor: str | None
    ) -> IngestTriggerOut:
        """立即执行一个采集任务（admin），写审计、失效缓存并广播。"""
        target = on_date or date.today()
        result = await trigger_task(task, target, repos=self._repos)
        await self._repos.audit_logs.record(
            actor=actor,
            action="ingest_trigger",
            target=task,
            detail={"trade_date": target.isoformat(), "status": result.status, "rows": result.rows},
        )
        await self._policy.invalidate("ingest")
        await self._policy.invalidate("pool")
        await self._policy.invalidate("sentiment_live")
        await _notify_ws(result)
        return IngestTriggerOut(
            task=result.task,
            capability=result.capability,
            trade_date=result.trade_date,
            status=result.status,
            rows=result.rows,
            source=result.source,
            job_id=result.job_id,
            error=result.error,
            attempts=result.attempts,
            duration_ms=result.duration_ms,
        )


async def _notify_ws(result: Any) -> None:
    """采集完成后向 WS ``alert`` 频道广播（失败仅记日志）。"""
    try:
        from app.api.ws import broadcast

        await broadcast(
            "alert",
            {
                "source": f"ingest:{result.capability}",
                "message": f"采集任务 {result.task} {result.status}（{result.rows} 行）",
                "status": result.status,
                "rows": result.rows,
                "job_id": result.job_id,
            },
        )
    except Exception:  # pragma: no cover - WS 不可用不应影响采集结果
        logger.warning("ws_broadcast_failed", exc_info=True, extra={"task": result.task})
