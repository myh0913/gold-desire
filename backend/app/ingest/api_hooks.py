"""管理 API 钩子（供 Task 12 的 admin 接口调用）。

- :func:`trigger_task`：立即执行一个采集任务，返回其 :class:`IngestResult`；
- :func:`ingest_health`：把 ``IngestJobRepository.health_summary`` 包装为稳定结构
  （每能力的 ``last_success`` / ``last_failure`` / ``consecutive_failures``）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.ingest.pipeline import IngestResult, ProviderLike, run_task
from app.ingest.tasks import get_task
from app.repositories import Repositories

__all__ = ["ingest_health", "trigger_task"]


async def trigger_task(
    name: str,
    trade_date: date,
    *,
    repos: Repositories,
    settings: Settings | None = None,
    provider_override: ProviderLike | None = None,
) -> IngestResult:
    """立即执行一个采集任务并返回结果。

    Args:
        name: 任务名（见 :func:`app.ingest.tasks.get_task`）。
        trade_date: 目标交易日。
        repos: 仓储容器。
        settings: 运行配置；缺省读全局配置。
        provider_override: 可注入 provider（测试）。

    Raises:
        NotFoundError: 任务名未注册。
    """
    try:
        defn = get_task(name)
    except KeyError as exc:
        raise NotFoundError(str(exc), detail={"task": name}) from exc
    return await run_task(
        defn,
        trade_date,
        repos=repos,
        settings=settings or get_settings(),
        provider_override=provider_override,
    )


def _iso(value: datetime | None) -> str | None:
    """时间转 ISO 串；``None`` 原样返回。"""
    return value.isoformat() if value is not None else None


async def ingest_health(repos: Repositories, since: datetime) -> dict[str, Any]:
    """采集健康度汇总（稳定结构，供 admin 接口与 Agent 使用）。

    Args:
        repos: 仓储容器。
        since: 统计起点（含）。
    """
    summaries = await repos.ingest_jobs.health_summary(since)
    capabilities: dict[str, dict[str, Any]] = {
        summary.capability: {
            "last_success": _iso(summary.last_success_at),
            "last_failure": _iso(summary.last_failure_at),
            "consecutive_failures": summary.consecutive_failures,
        }
        for summary in summaries
    }
    return {"since": since.isoformat(), "capabilities": capabilities}
