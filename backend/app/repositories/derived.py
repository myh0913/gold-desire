"""派生产物仓储（``derived_*`` 语义）：建议报告、回测任务、采集任务。

- :class:`AdviceReportRepository`：建议报告按 ``(trade_date, kind, strategy_id, ran_at)`` 幂等覆盖。
- :class:`BacktestRunRepository`：回测任务状态机（pending → running → succeeded/failed）。
- :class:`IngestJobRepository`：采集任务明细与**健康度汇总**（各能力最近成功/失败 + 连续失败次数）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select

from app.models.derived import AdviceReport, BacktestRun, IngestJob
from app.repositories.base import BaseRepository

_TERMINAL_STATUSES = frozenset({"succeeded", "failed"})
_ADVICE_CONFLICT = ("trade_date", "kind", "strategy_id", "ran_at")
_ADVICE_UPDATE = ("strategy_version", "payload")


def _utcnow() -> datetime:
    """当前 UTC 时间（统一时区口径，避免本地时区混用）。"""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CapabilityHealth:
    """单个采集能力的健康度摘要。

    Attributes:
        capability: 能力标识。
        last_success_at: 最近一次成功时间。
        last_failure_at: 最近一次失败时间。
        consecutive_failures: 自最近一次非失败任务起的**连续失败**次数。
    """

    capability: str
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int


class AdviceReportRepository(BaseRepository):
    """建议报告仓储。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """按 ``(trade_date, kind, strategy_id, ran_at)`` 幂等覆盖写入建议报告。"""
        return await self.bulk_upsert(AdviceReport, rows, _ADVICE_CONFLICT, _ADVICE_UPDATE)

    async def get_by_date(
        self, trade_date: date, kind: str | None = None, strategy_id: str | None = None
    ) -> list[AdviceReport]:
        """取某日建议报告，可按类型/策略过滤，按运行时间倒序。"""
        stmt = select(AdviceReport).where(AdviceReport.trade_date == trade_date)
        if kind is not None:
            stmt = stmt.where(AdviceReport.kind == kind)
        if strategy_id is not None:
            stmt = stmt.where(AdviceReport.strategy_id == strategy_id)
        stmt = stmt.order_by(AdviceReport.ran_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest(self, trade_date: date) -> AdviceReport | None:
        """取某日最近一次运行的建议报告。"""
        stmt = (
            select(AdviceReport)
            .where(AdviceReport.trade_date == trade_date)
            .order_by(AdviceReport.ran_at.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def list_dates(self, limit: int = 30) -> list[date]:
        """有建议报告的交易日列表（去重，倒序），供历史下拉。"""
        stmt = (
            select(AdviceReport.trade_date)
            .distinct()
            .order_by(AdviceReport.trade_date.desc())
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return [value for value in result.scalars().all() if isinstance(value, date)]

    async def list_range(self, start: date, end: date) -> list[AdviceReport]:
        """取 ``[start, end]`` 区间内全部建议报告，按交易日、运行时间倒序。"""
        stmt = (
            select(AdviceReport)
            .where(AdviceReport.trade_date.between(start, end))
            .order_by(AdviceReport.trade_date.desc(), AdviceReport.ran_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class BacktestRunRepository(BaseRepository):
    """回测任务仓储。"""

    async def create(
        self,
        run_id: str,
        start_date: date,
        end_date: date,
        strategies: Sequence[str],
        params: dict[str, Any],
        *,
        created_by: str | None = None,
    ) -> BacktestRun:
        """新建回测任务（初始状态 ``pending``）。"""
        row = BacktestRun(
            run_id=run_id,
            status="pending",
            start_date=start_date,
            end_date=end_date,
            strategies=list(strategies),
            params=params,
            created_by=created_by,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_status(
        self,
        run_id: str,
        status: str,
        report: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> BacktestRun | None:
        """推进任务状态；进入 ``running`` 记开始时间，进入终态记结束时间。

        返回更新后的行；``run_id`` 不存在时返回 ``None``。
        """
        row = await self.get(run_id)
        if row is None:
            return None
        row.status = status
        if status == "running" and row.started_at is None:
            row.started_at = _utcnow()
        if status in _TERMINAL_STATUSES:
            row.finished_at = _utcnow()
        if report is not None:
            row.report = report
        if error is not None:
            row.error = error
        await self.session.flush()
        return row

    async def get(self, run_id: str) -> BacktestRun | None:
        """按 ``run_id`` 取回测任务。"""
        stmt = select(BacktestRun).where(BacktestRun.run_id == run_id)
        return await self.session.scalar(stmt)

    async def list_recent(self, limit: int = 20) -> list[BacktestRun]:
        """按创建时间倒序取最近回测任务。"""
        stmt = select(BacktestRun).order_by(BacktestRun.id.desc()).limit(max(1, limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class IngestJobRepository(BaseRepository):
    """采集任务明细仓储。"""

    async def start(
        self, job_id: str, capability: str, source: str, trade_date: date | None
    ) -> IngestJob:
        """登记采集任务开始（状态 ``running``）。"""
        row = IngestJob(
            job_id=job_id,
            capability=capability,
            source=source,
            trade_date=trade_date,
            status="running",
            started_at=_utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def finish(
        self, job_id: str, status: str, rows: int, error: str | None = None
    ) -> IngestJob | None:
        """登记采集任务结束（状态、行数、错误、结束时间）。"""
        stmt = select(IngestJob).where(IngestJob.job_id == job_id)
        row = await self.session.scalar(stmt)
        if row is None:
            return None
        row.status = status
        row.rows = rows
        row.error = error
        row.finished_at = _utcnow()
        await self.session.flush()
        return row

    async def get(self, job_id: str) -> IngestJob | None:
        """按 ``job_id`` 取采集任务。"""
        stmt = select(IngestJob).where(IngestJob.job_id == job_id)
        return await self.session.scalar(stmt)

    async def list_recent(self, limit: int = 50) -> list[IngestJob]:
        """按开始时间倒序取最近采集任务。"""
        stmt = select(IngestJob).order_by(IngestJob.id.desc()).limit(max(1, limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def health_summary(self, since: datetime) -> list[CapabilityHealth]:
        """按能力汇总采集健康度：最近成功/失败时间与连续失败次数。

        Args:
            since: 统计起点（含），仅纳入该时间之后开始的任务。
        """
        stmt = (
            select(IngestJob)
            .where(IngestJob.started_at >= since)
            .order_by(IngestJob.capability, IngestJob.started_at.desc())
        )
        result = await self.session.execute(stmt)
        grouped: dict[str, list[IngestJob]] = {}
        for job in result.scalars().all():
            grouped.setdefault(job.capability, []).append(job)

        summaries: list[CapabilityHealth] = []
        for capability in sorted(grouped):
            jobs = grouped[capability]
            last_success = next(
                (job.finished_at or job.started_at for job in jobs if job.status == "succeeded"),
                None,
            )
            last_failure = next(
                (job.finished_at or job.started_at for job in jobs if job.status == "failed"),
                None,
            )
            consecutive = 0
            for job in jobs:
                if job.status == "failed":
                    consecutive += 1
                else:
                    break
            summaries.append(
                CapabilityHealth(
                    capability=capability,
                    last_success_at=last_success,
                    last_failure_at=last_failure,
                    consecutive_failures=consecutive,
                )
            )
        return summaries


__all__ = [
    "AdviceReportRepository",
    "BacktestRunRepository",
    "CapabilityHealth",
    "IngestJobRepository",
]
