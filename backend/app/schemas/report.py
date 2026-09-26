"""报告域响应契约：建议报告与回测任务。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AdviceLatestResponse",
    "AdviceMarkOut",
    "AdviceMarkRequest",
    "AdviceMarksResponse",
    "AdviceReportOut",
    "AdviceResponse",
    "BacktestRunOut",
    "BacktestRunRequest",
    "BacktestRunsResponse",
    "DragonPoolItemOut",
    "DragonPoolResponse",
]

_ORM = ConfigDict(from_attributes=True)


class DragonPoolItemOut(BaseModel):
    """盘后建池候选行（``dragon_pool``，供次日开盘判定参考）。"""

    model_config = _ORM

    trade_date: date
    strategy_id: str
    code: str
    name: str | None = None
    d_date: date
    boards: int
    d_amp_pct: float | None = None
    shape_label: str | None = None
    ran_at: datetime


class DragonPoolResponse(BaseModel):
    """某交易日盘后建池候选列表。"""

    trade_date: date | None = None
    strategy_id: str | None = None
    items: list[DragonPoolItemOut] = Field(default_factory=list)


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


class AdviceMarkOut(BaseModel):
    """建议「已买入」标记行。"""

    model_config = _ORM

    trade_date: date
    strategy_id: str
    code: str
    marked_by: str | None = None
    marked_at: datetime | None = None


class AdviceMarksResponse(BaseModel):
    """某交易日已买入标记列表。"""

    trade_date: date
    items: list[AdviceMarkOut] = Field(default_factory=list)


class AdviceMarkRequest(BaseModel):
    """标记/取消「已买入」（取消 = 删行）。"""

    trade_date: date = Field(description="建议交易日")
    strategy_id: str = Field(min_length=1, max_length=64, description="策略标识")
    code: str = Field(min_length=1, max_length=16, description="股票代码")
    bought: bool = Field(description="true=标记已买入；false=取消")


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
    """触发一次回测（异步执行：立即返回 running 任务行，完成/失败由后台任务更新）。"""

    start: date = Field(description="回测起始日（含）")
    end: date = Field(description="回测结束日（含）")
    strategy_id: str = Field(default="dragon", max_length=64, description="策略标识")
    params: dict[str, Any] = Field(default_factory=dict, description="策略参数覆盖")
