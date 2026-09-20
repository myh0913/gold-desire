"""统一取数入口：**只做映射 + 校验，无任何源分支**。

调用方（策略/服务/API）一律::

    from app.datasources.resolve import resolve
    rows = await resolve("limit_up_pool", date="2026-09-18")

本层职责：
1. 按 :func:`_eligible_order` 顺序遍历候选源（生产环境剔除 fake 兜底源）；
2. 每源：``provider.fetch()`` → ``apply_mapping()`` → ``model_validate()``；
3. 单源失败（``UpstreamError`` / ``ContractValidationError``）记录结构化告警后
   自动降级到下一源；全部失败则抛 ``UpstreamError``（列出各源失败原因）。

**回放防未来函数**：传入 ``replay_date`` 时本层不调用任何 provider，直接抛
:class:`SnapshotMissingError`，要求调用方走仓储/快照读路径。Task 7 可通过
:func:`set_replay_source` 注入 DB 快照读取器。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.core.config import get_settings
from app.core.errors import SnapshotMissingError, UpstreamError
from app.datasources.contracts import (
    CAPABILITY_CONTRACTS,
    ContractModel,
    ContractValidationError,
    validate_records,
)
from app.datasources.mappings.dsl import MappingError, apply_mapping
from app.datasources.mappings.registry import get_mapping
from app.datasources.registry import get_provider, resolve_order

__all__ = [
    "RawPayload",
    "reset_replay_source",
    "resolve",
    "resolve_raw",
    "set_replay_source",
]

logger = logging.getLogger(__name__)

ReplayReader = Callable[..., Awaitable[list[ContractModel]]]

_replay_source: ReplayReader | None = None

#: fake 源 id：仅在非生产环境作为兜底候选（见 :func:`_eligible_order`）。
FAKE_SOURCE_ID = "fake"


@dataclass(frozen=True)
class RawPayload:
    """原始取数结果（供采集管道 Task 7 留档 ``raw_*``）。"""

    source_id: str
    capability: str
    payload: dict[str, Any]


def set_replay_source(reader: ReplayReader | None) -> None:
    """注入回放读取器（Task 7：从 DB 快照读）。传 ``None`` 清除。"""
    global _replay_source
    _replay_source = reader


def reset_replay_source() -> None:
    """清除回放读取器（测试隔离用）。"""
    set_replay_source(None)


def _contract_for(capability: str) -> type[ContractModel]:
    model = CAPABILITY_CONTRACTS.get(capability)
    if model is None:
        raise UpstreamError(
            f"未知能力 {capability!r}（已声明：{sorted(CAPABILITY_CONTRACTS)}）",
            detail={"capability": capability},
        )
    return model


def _failure(source_id: str, exc: Exception) -> dict[str, Any]:
    """把一次失败整理为结构化上下文。"""
    if isinstance(exc, ContractValidationError):
        return {
            "source": source_id,
            "kind": "contract_validation_error",
            "message": exc.message,
            "issues": exc.to_detail()["issues"],
        }
    return {"source": source_id, "kind": type(exc).__name__, "message": str(exc)}


def _eligible_order(capability: str) -> list[str]:
    """返回该能力的候选源顺序，必要时剔除 fake 兜底源。

    生产环境（``settings.fake_fallback_enabled`` 为 ``False``）剔除 fake，使得真实源
    全挂时**显式抛错**，而不是把固定假数据当真实行情写进库。
    """
    order = resolve_order(capability)
    if get_settings().fake_fallback_enabled:
        return order
    return [source_id for source_id in order if source_id != FAKE_SOURCE_ID]


def _log_failure(
    capability: str, source_id: str, exc: Exception, *, next_source: str | None
) -> None:
    """记录一次取数失败。

    Args:
        next_source: 降级目标源；``None`` 表示无候选源可降级（记 ERROR，调用方随后抛错），
            等于 ``FAKE_SOURCE_ID`` 时记 WARNING 并显式说明「本次返回的是假数据」。
    """
    if next_source is None:
        level, message = logging.ERROR, "数据源取数失败，无更多候选源"
    elif next_source == FAKE_SOURCE_ID:
        level, message = (
            logging.WARNING,
            "数据源取数失败，降级到 fake 源（返回假数据，仅限非生产环境）",
        )
    else:
        level, message = logging.WARNING, "数据源取数失败，降级到下一源"

    logger.log(
        level,
        message,
        extra={
            "capability": capability,
            "source": source_id,
            "next_source": next_source,
            "error": _failure(source_id, exc),
        },
    )


async def _resolve_from_replay(
    capability: str, replay_date: date, args: dict[str, Any]
) -> list[ContractModel]:
    """回放路径：禁止实时取数；缺读取器则显式报错。"""
    if _replay_source is None:
        raise SnapshotMissingError(
            f"回放模式（replay_date={replay_date.isoformat()}）不调用实时数据源："
            "请经仓储/快照读路径获取历史数据（采集侧 Task 7 通过 set_replay_source 注入）",
            detail={"capability": capability, "replay_date": replay_date.isoformat()},
        )
    return await _replay_source(capability, replay_date=replay_date, **args)


async def resolve(
    capability: str,
    *,
    replay_date: date | None = None,
    **args: Any,
) -> list[ContractModel]:
    """按能力取数并返回契约对象列表（主源失败自动降级）。

    Args:
        capability: 能力名，须在 ``CAPABILITY_CONTRACTS`` 中。
        replay_date: 回放日期；提供时禁止实时取数（抛 ``SnapshotMissingError``）。
        **args: 透传给 ``provider.fetch`` 的取数参数（如 ``date``、``code``）。

    Raises:
        SnapshotMissingError: 回放模式下未注入快照读取器。
        UpstreamError: 全部候选源失败（``detail["failures"]`` 列出各源原因）。
    """
    if replay_date is not None:
        return await _resolve_from_replay(capability, replay_date, args)

    model = _contract_for(capability)
    order = _eligible_order(capability)
    if not order:
        raise UpstreamError(
            f"能力 {capability!r} 无可用数据源"
            f"（fake 兜底已禁用：app_env={get_settings().app_env}）",
            detail={"capability": capability},
        )

    failures: list[dict[str, Any]] = []
    for index, source_id in enumerate(order):
        next_source = order[index + 1] if index < len(order) - 1 else None
        try:
            provider = get_provider(source_id)()
            await provider.acquire()
            payload = await provider.fetch(capability, **args)
            mapping = get_mapping(source_id, capability)
            rows = apply_mapping(mapping, payload, context={"args": args})
            return validate_records(model, rows, source=source_id, capability=capability)
        except (UpstreamError, ContractValidationError, MappingError) as exc:
            failures.append(_failure(source_id, exc))
            _log_failure(capability, source_id, exc, next_source=next_source)

    raise UpstreamError(
        f"能力 {capability!r} 的全部数据源失败（共 {len(failures)} 个）",
        detail={"capability": capability, "failures": failures},
    )


async def resolve_raw(
    capability: str,
    *,
    replay_date: date | None = None,
    **args: Any,
) -> RawPayload:
    """取回**原始** payload（不做映射/校验），供采集管道留档。

    与 :func:`resolve` 一样遵循主备降级与回放守卫。
    """
    if replay_date is not None:
        await _resolve_from_replay(capability, replay_date, args)
        raise SnapshotMissingError(
            f"回放模式（replay_date={replay_date.isoformat()}）不支持原始取数",
            detail={"capability": capability, "replay_date": replay_date.isoformat()},
        )

    _contract_for(capability)
    order = _eligible_order(capability)
    if not order:
        raise UpstreamError(
            f"能力 {capability!r} 无可用数据源"
            f"（fake 兜底已禁用：app_env={get_settings().app_env}）",
            detail={"capability": capability},
        )

    failures: list[dict[str, Any]] = []
    for index, source_id in enumerate(order):
        next_source = order[index + 1] if index < len(order) - 1 else None
        try:
            provider = get_provider(source_id)()
            await provider.acquire()
            payload = await provider.fetch(capability, **args)
            return RawPayload(source_id=source_id, capability=capability, payload=payload)
        except (UpstreamError, ContractValidationError, MappingError) as exc:
            failures.append(_failure(source_id, exc))
            _log_failure(capability, source_id, exc, next_source=next_source)

    raise UpstreamError(
        f"能力 {capability!r} 的全部数据源失败（共 {len(failures)} 个）",
        detail={"capability": capability, "failures": failures},
    )
