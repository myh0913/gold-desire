"""报告域响应契约：建议报告与回测任务。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AdviceLatestResponse",
    "AdviceReportOut",
    "AdviceResponse",
    "BacktestRunOut",
    "BacktestRunRequest",
    "BacktestRunsResponse",
]

_ORM = ConfigDict(from_attributes=True)


class AdviceReportOut(BaseModel):
    """策略建议报告（``payload`` 为结构化建议记录，含门槛/加分项/仓位/止损）。"""

    model_config = _ORM

    trade_date: date
    kind: str
    strategy_id: str
    strategy_version: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ran_at: datetime
    created_at: datetime | None = None


class AdviceResponse(BaseModel):
    """某交易日建议报告列表。"""

    trade_date: date | None = None
    kind: str | None = None
    strategy_id: str | None = None
    items: list[AdviceReportOut] = Field(default_factory=list)


class AdviceLatestResponse(BaseModel):
    """某交易日最近一次运行的建议报告。"""

    trade_date: date | None = None
    item: AdviceReportOut | None = None


class BacktestRunOut(BaseModel):
    """回测任务（``report`` 含 A/B/C 三段，未完成时为 ``None``）。"""

    model_config = _ORM

    run_id: str
    status: str
    start_date: date
    end_date: date
    strategies: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_by: str | None = None


class BacktestRunsResponse(BaseModel):
    """最近回测任务列表（条数受 ``limit`` 约束）。"""

    limit: int
    items: list[BacktestRunOut] = Field(default_factory=list)


class BacktestRunRequest(BaseModel):
    """触发一次回测（同步执行，返回 run 记录）。"""

    start: date = Field(description="回测起始日（含）")
    end: date = Field(description="回测结束日（含）")
    strategy_id: str = Field(default="dragon", max_length=64, description="策略标识")
    params: dict[str, Any] = Field(default_factory=dict, description="策略参数覆盖")
