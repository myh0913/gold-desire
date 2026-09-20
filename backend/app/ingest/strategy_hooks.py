"""策略阶段执行钩子：盘后数据齐备后自动运行策略并推送 WS。

**时机**（与 :mod:`app.ingest.windows` 的窗口对齐）：策略阶段的输入是**已入库的
完整日线 / 分时**（:func:`app.engine.dragon_samples.build_samples` 要求 D+1 / D+2
日线均已存在），故挂在 **postmarket（17:00-18:00）采集完成之后**：

- ``Phase.POOL`` → ``build_pool``：识别当日新增的龙回头候选结构（D = 当日）；
- ``Phase.INTRADAY`` → ``confirm_intraday``：对窗口内样本按 S2/S4 硬门槛判定，
  产出结构化建议落 ``advice_reports``（买点为 T / T1 开盘，记录口径见各建议
  ``buy_day``）。

> 说明：真·盘中 09:25 实时判定需要竞价/实时行情源，当前数据源能力不含此项；
> 本钩子产出的是**数据齐备后的确认记录**，供「今日建议 / 复盘」消费。

**幂等**：每阶段每日状态落 ``pool_snapshot``（``pool_name='strategy_state:<phase>'``），
成功不重跑；失败不标记成功，窗口内随调度 tick 重试（与采集任务同语义）。

**推送**：POOL 完成 → WS ``pool`` 频道；INTRADAY 产出建议 → WS ``advice`` 频道
（经 :mod:`app.core.ws_bus` 跨进程中转，见该模块说明）。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from app.core.config import Settings
from app.repositories import Repositories
from app.strategies.context import StrategyContextFactory
from app.strategies.protocol import Phase
from app.strategies.registry import PhaseRunSummary, run_phase

__all__ = ["POSTMARKET_PHASES", "run_strategy_phases", "strategy_state_name"]

logger = logging.getLogger(__name__)

#: 盘后窗口内依次执行的策略阶段。
POSTMARKET_PHASES: tuple[Phase, ...] = (Phase.POOL, Phase.INTRADAY)

_STATE_PREFIX = "strategy_state"


def strategy_state_name(phase: Phase) -> str:
    """策略阶段当日状态在 ``pool_snapshot`` 中的池名。"""
    return f"{_STATE_PREFIX}:{phase.value}"


async def _state_status(repos: Repositories, phase: Phase, trade_date: date) -> str | None:
    """读取某阶段某日状态（``succeeded`` / ``failed`` / ``None``）。"""
    row = await repos.pool_snapshot.get(trade_date, strategy_state_name(phase))
    if row is None:
        return None
    status = row.payload.get("status")
    return str(status) if status else None


async def _state_mark(
    repos: Repositories, phase: Phase, trade_date: date, status: str
) -> None:
    """落库阶段状态（幂等键 ``(trade_date, pool_name)``）。"""
    await repos.pool_snapshot.upsert_many(
        [
            {
                "trade_date": trade_date,
                "pool_name": strategy_state_name(phase),
                "payload": {
                    "status": status,
                    "at": datetime.now(UTC).isoformat(),
                },
                "source": "strategy_hooks",
            }
        ]
    )


async def _broadcast_phase(phase: Phase, trade_date: date, summary: PhaseRunSummary) -> None:
    """阶段完成后推送 WS（尽力而为，失败只记日志）。"""
    from app.core.ws_bus import publish_event

    try:
        if phase is Phase.POOL:
            candidates: list[dict[str, Any]] = []
            for result in summary.results:
                if result.ok and isinstance(result.output, dict):
                    raw = result.output.get("candidates")
                    if isinstance(raw, list):
                        candidates = [
                            item for item in raw if isinstance(item, dict)
                        ][:20]
            await publish_event(
                "pool",
                {
                    "source": "strategy:pool",
                    "trade_date": trade_date.isoformat(),
                    "count": len(candidates),
                    "candidates": candidates,
                },
            )
            return
        if phase is Phase.INTRADAY:
            advices: list[dict[str, Any]] = []
            for result in summary.results:
                if result.ok and isinstance(result.output, dict):
                    raw = result.output.get("advices")
                    if isinstance(raw, list):
                        advices.extend(item for item in raw if isinstance(item, dict))
            if advices:
                await publish_event(
                    "advice",
                    {
                        "source": "strategy:dragon",
                        "trade_date": trade_date.isoformat(),
                        "count": len(advices),
                        "advices": advices,
                    },
                )
    except Exception:  # pragma: no cover - 推送失败不影响策略结果
        logger.warning("strategy_phase_broadcast_failed", extra={"phase": phase.value})


async def run_strategy_phases(
    repos: Repositories,
    settings: Settings,
    trade_date: date,
    *,
    phases: tuple[Phase, ...] = POSTMARKET_PHASES,
) -> list[PhaseRunSummary]:
    """依次执行给定策略阶段（已成功的跳过），返回本轮实际运行的汇总列表。

    单个策略失败由 :func:`run_phase` 隔离（错误落 ``advice_reports`` 可查询）；
    阶段内任一策略失败则当日状态记 ``failed``（窗口内重试），全成功记 ``succeeded``。
    """
    executed: list[PhaseRunSummary] = []
    for phase in phases:
        if await _state_status(repos, phase, trade_date) == "succeeded":
            continue
        factory = StrategyContextFactory(repos=repos, settings=settings, trade_date=trade_date)
        try:
            summary = await run_phase(phase, factory, repos)
        except Exception as exc:
            logger.exception(
                "strategy_phase_crashed",
                extra={"phase": phase.value, "trade_date": trade_date.isoformat()},
            )
            summary = PhaseRunSummary(phase=phase, trade_date=trade_date, results=())
            summary_failed_marker = f"{type(exc).__name__}: {exc}"
        else:
            summary_failed_marker = None
        status = "succeeded" if summary.failure_count == 0 and summary_failed_marker is None else "failed"
        await _state_mark(repos, phase, trade_date, status)
        logger.info(
            "strategy_phase_done",
            extra={
                "phase": phase.value,
                "trade_date": trade_date.isoformat(),
                "status": status,
                "succeeded": summary.success_count,
                "failed": summary.failure_count,
            },
        )
        executed.append(summary)
        if status == "succeeded":
            await _broadcast_phase(phase, trade_date, summary)
    return executed
