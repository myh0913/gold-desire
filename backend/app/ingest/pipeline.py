"""采集执行器：把一次「能力取数」走完 raw 留档 → 契约校验 → 幂等入库 → 任务留痕。

一次 :func:`run_task` 的步骤：

1. 建 ``ingest_jobs`` 行（``start``，状态 ``running``，``attempts`` 自增）；
2. 由任务声明构造**一组**取数参数（``args_builder``），逐份取原始 payload
   （``resolve_raw``，主备降级 + 限流），经 ``raw_responses`` 留档；
3. 契约校验（与 ``resolve()`` 相同的映射 + 校验原语）；
4. 由任务声明的 writer 把契约行幂等覆盖写（``bulk_upsert``，键见各仓储），行数累加；
5. ``finish`` 任务行（状态 / 行数 / 错误 / 结束时间）。

**多轮取数**：一份任务可产生多轮取数（如 ``daily_bars`` 按单只证券取历史区间），
每一轮独立留档、独立校验、独立入库；参数为空则视为「无标的可采」，按成功 0 行处理。

**幂等性**：行级由 ``bulk_upsert`` 的冲突键保证（重跑不产生重复行）；任务级由调度器
的完成状态保证（见 :mod:`app.ingest.scheduler`）。

**不静默吞错**：任何失败都会写入 ``ingest_jobs``（``status='failed'`` + ``error``），
并作为失败结果返回；:func:`run_many` 逐任务隔离，单任务失败不影响其余任务。

取数只做一次（``resolve_raw``）：同一轮的「留档」与「契约」共用同一份 payload，
避免重复触达上游、浪费限频预算。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Protocol, runtime_checkable

from app.core.config import Settings
from app.core.errors import UpstreamError
from app.datasources.contracts import CAPABILITY_CONTRACTS, ContractModel, validate_records
from app.datasources.mappings.dsl import apply_mapping
from app.datasources.mappings.registry import get_mapping
from app.datasources.registry import resolve_order
from app.datasources.resolve import RawPayload, resolve_raw
from app.ingest.tasks import WRITERS, IngestTaskDef
from app.repositories import Repositories

__all__ = [
    "IngestResult",
    "ProviderLike",
    "contracts_from_raw",
    "fetch_raw",
    "run_many",
    "run_task",
]

logger = logging.getLogger(__name__)


@runtime_checkable
class ProviderLike(Protocol):
    """可注入 provider 的结构协议（测试/手动触发用，避免依赖具体 provider）。"""

    source_id: str

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        """按能力返回原始 payload。"""
        ...


@dataclass(frozen=True, slots=True)
class IngestResult:
    """一次采集任务的执行结果。

    Attributes:
        task: 任务名。
        capability: 能力名。
        trade_date: 目标交易日。
        status: ``succeeded`` / ``failed``。
        rows: 入库行数（失败为 0）。
        source: 实际取数来源标识。
        job_id: ``ingest_jobs.job_id``。
        error: 失败原因（成功为 ``None``）。
        attempts: 该次执行的尝试序号（从 1 起）。
        duration_ms: 执行耗时（毫秒）。
    """

    task: str
    capability: str
    trade_date: date
    status: str
    rows: int
    source: str
    job_id: str
    error: str | None
    attempts: int
    duration_ms: int


def _primary_source(capability: str) -> str:
    """能力的主源标识（用于建任务行时的来源占位）。"""
    order = resolve_order(capability)
    return order[0] if order else "unknown"


async def fetch_raw(
    capability: str,
    args: dict[str, Any],
    *,
    provider_override: ProviderLike | None = None,
) -> RawPayload:
    """取原始 payload：注入 override 时直连之，否则走 ``resolve_raw``（主备降级）。

    计时填入 ``elapsed_ms``（含主备降级的总耗时）；成功路径 ``http_status``
    恒 200（失败请求不落 raw_responses，由 ingest_jobs 错误记录承载）。

    Args:
        capability: 能力名。
        args: 透传给 ``provider.fetch`` 的取数参数。
        provider_override: 测试/手动注入的 provider；``None`` 走注册表主备链。
    """
    started = time.perf_counter()
    if provider_override is not None:
        payload = await provider_override.fetch(capability, **args)
        return RawPayload(
            source_id=provider_override.source_id or "override",
            capability=capability,
            payload=payload,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            http_status=200,
        )
    raw = await resolve_raw(capability, **args)
    return replace(
        raw,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        http_status=200,
    )


def contracts_from_raw(
    raw: RawPayload, args: dict[str, Any] | None = None
) -> list[ContractModel]:
    """把原始 payload 经声明式映射 + 契约校验转为契约对象列表。

    与 :func:`app.datasources.resolve.resolve` 使用相同的映射/校验原语，故结果等价；
    区别仅在于**复用同一份 payload**，不再重复取数。

    Args:
        raw: 原始取数结果。
        args: 本次取数参数；供映射的 ``context`` 取值（如题材类响应不含日期，
            需从 ``args.date`` 派生 ``trade_date``）。

    Raises:
        UpstreamError: 能力无契约。
        MappingError / ContractValidationError: 映射或校验失败。
    """
    model = CAPABILITY_CONTRACTS.get(raw.capability)
    if model is None:
        raise UpstreamError(
            f"未知能力 {raw.capability!r}",
            detail={"capability": raw.capability},
        )
    mapping = get_mapping(raw.source_id, raw.capability)
    rows = apply_mapping(mapping, raw.payload, context={"args": args or {}})
    return validate_records(model, rows, source=raw.source_id, capability=raw.capability)


def _args_hash(defn: IngestTaskDef, trade_date: date, args: dict[str, Any]) -> str:
    """原始响应留档的幂等键：幂等键 + 取数参数的规范化哈希。"""
    blob = json.dumps(
        {"key": defn.idempotency_key(defn.capability, trade_date), "args": args},
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _payload_sha256(payload: dict[str, Any]) -> str:
    """原始载荷内容哈希（变更检测）。"""
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def run_task(
    defn: IngestTaskDef,
    trade_date: date,
    *,
    repos: Repositories,
    settings: Settings,
    provider_override: ProviderLike | None = None,
) -> IngestResult:
    """执行一个采集任务，返回结果（失败也返回结果，绝不静默）。

    Args:
        defn: 任务定义。
        trade_date: 目标交易日。
        repos: 仓储容器（与调用方共享会话）。
        settings: 运行配置。
        provider_override: 可注入的 provider（测试/手动）。
    """
    started = time.perf_counter()
    job_id = uuid.uuid4().hex
    job = await repos.ingest_jobs.start(
        job_id, defn.capability, _primary_source(defn.capability), trade_date
    )
    job.attempts = int(job.attempts or 0) + 1
    await repos.session.flush()
    log_extra: dict[str, Any] = {
        "task": defn.name,
        "capability": defn.capability,
        "trade_date": trade_date.isoformat(),
        "job_id": job_id,
        "env": settings.app_env,
    }
    logger.info("ingest_task_started", extra=log_extra)

    try:
        args_list = await defn.args_builder(trade_date, repos)
        writer = WRITERS[defn.target]
        rows = 0
        for args in args_list:
            raw = await fetch_raw(defn.capability, dict(args), provider_override=provider_override)
            job.source = raw.source_id
            await repos.session.flush()
            await repos.raw_responses.save(
                source=raw.source_id,
                capability=defn.capability,
                args_hash=_args_hash(defn, trade_date, args),
                trade_date=trade_date,
                payload=raw.payload,
                sha256=_payload_sha256(raw.payload),
                elapsed_ms=raw.elapsed_ms,
                http_status=raw.http_status,
            )
            contracts = contracts_from_raw(raw, dict(args))
            rows += await writer(repos, contracts, trade_date, raw.source_id)
        await repos.ingest_jobs.finish(job_id, "succeeded", rows)
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "ingest_task_succeeded",
            extra={
                **log_extra,
                "source": job.source,
                "rows": rows,
                "rounds": len(args_list),
            },
        )
        return IngestResult(
            task=defn.name,
            capability=defn.capability,
            trade_date=trade_date,
            status="succeeded",
            rows=rows,
            source=job.source,
            job_id=job_id,
            error=None,
            attempts=job.attempts,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        await repos.ingest_jobs.finish(job_id, "failed", 0, str(exc))
        logger.exception(
            "ingest_task_failed", extra={**log_extra, "source": job.source, "error": str(exc)}
        )
        return IngestResult(
            task=defn.name,
            capability=defn.capability,
            trade_date=trade_date,
            status="failed",
            rows=0,
            source=job.source,
            job_id=job_id,
            error=str(exc),
            attempts=job.attempts,
            duration_ms=duration_ms,
        )


async def run_many(
    tasks: Sequence[IngestTaskDef],
    trade_date: date,
    *,
    repos: Repositories,
    settings: Settings,
    provider_override: ProviderLike | None = None,
) -> list[IngestResult]:
    """逐个执行任务，**逐任务隔离**：单任务失败不影响其余任务。

    Returns:
        与 ``tasks`` 等长的结果列表（成功/失败均在内）。
    """
    results: list[IngestResult] = []
    for defn in tasks:
        try:
            results.append(
                await run_task(
                    defn,
                    trade_date,
                    repos=repos,
                    settings=settings,
                    provider_override=provider_override,
                )
            )
        except Exception as exc:  # run_task 通常不抛，这里兜底保证隔离
            logger.exception("ingest_task_crashed", extra={"task": defn.name, "error": str(exc)})
            results.append(
                IngestResult(
                    task=defn.name,
                    capability=defn.capability,
                    trade_date=trade_date,
                    status="failed",
                    rows=0,
                    source="unknown",
                    job_id="",
                    error=str(exc),
                    attempts=0,
                    duration_ms=0,
                )
            )
    return results
