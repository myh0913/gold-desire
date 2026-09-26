"""派生产物模型（``derived_*`` 语义）：建议报告、回测任务、采集任务与 Agent 审计。

- 建议报告按 ``(trade_date, kind, strategy_id, ran_at, code)`` 唯一：同一次运行的多条
  建议（不同股票）互不覆盖，同日多次重跑按 ``ran_at`` 留痕。
- 采集任务以 ``job_id`` 唯一，幂等键为 ``(capability, args, trade_date)``。
- Agent 会话/消息/工具调用全量留痕，供事后审计还原。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JsonType, TimestampMixin


class DragonPoolCandidate(Base):
    """龙回头盘后建池候选（Phase.POOL 识别的结构样本，供次日开盘判定参考）。

    快照语义：同一 ``(trade_date, strategy_id)`` 重跑时**整体替换**（先删后插），
    库中恒等于最近一次建池结果，不做行级 upsert。
    """

    __tablename__ = "dragon_pool"
    __table_args__ = (Index("ix_dragon_pool_date_strategy", "trade_date", "strategy_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, doc="建池交易日（D 日）")
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, doc="策略标识")
    code: Mapped[str] = mapped_column(String(16), nullable=False, doc="股票代码")
    name: Mapped[str | None] = mapped_column(String(64), nullable=True, doc="股票名称")
    d_date: Mapped[date] = mapped_column(Date, nullable=False, doc="首阴日（D）")
    boards: Mapped[int] = mapped_column(Integer, nullable=False, doc="首阴前连板数")
    d_amp_pct: Mapped[float | None] = mapped_column(
        Numeric(10, 4), nullable=True, doc="首阴日振幅（%）"
    )
    shape_label: Mapped[str | None] = mapped_column(
        String(32), nullable=True, doc="首阴日分时形态标签"
    )
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, doc="建池运行时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdviceReport(Base):
    """策略建议报告（结构化建议记录，含命中门槛、加分项、仓位、止损与卖出时点）。"""

    __tablename__ = "advice_reports"
    __table_args__ = (
        UniqueConstraint(
            "trade_date",
            "kind",
            "strategy_id",
            "ran_at",
            "code",
            name="uq_advice_reports_trade_kind_strategy_ran_code",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True, nullable=False, doc="交易日")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, doc="报告类型")
    strategy_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, doc="策略标识")
    code: Mapped[str | None] = mapped_column(
        String(16), nullable=True, doc="股票代码（建议行唯一键组成部分；error 行可空）"
    )
    strategy_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="所用策略参数版本号"
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, doc="报告结构化载荷")
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, doc="运行时间（决定唯一键）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdviceMark(Base):
    """建议「已买入」人工标记（建议页点选留痕，复盘页串联展示）。

    以 ``(trade_date, strategy_id, code)`` 唯一；取消买入 = 删行，
    表内恒为「已买入」集合，不做布尔翻转。
    """

    __tablename__ = "advice_marks"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "strategy_id", "code", name="uq_advice_marks_date_strategy_code"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True, nullable=False, doc="建议交易日")
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, doc="策略标识")
    code: Mapped[str] = mapped_column(String(16), nullable=False, doc="股票代码")
    marked_by: Mapped[str | None] = mapped_column(String(64), nullable=True, doc="标记人用户名")
    marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, doc="标记时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BacktestRun(Base):
    """回测任务及其产物（同区间同参数重跑结果幂等）。"""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False, doc="回测任务 ID"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", doc="状态：pending/running/succeeded/failed"
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, doc="回测起始日")
    end_date: Mapped[date] = mapped_column(Date, nullable=False, doc="回测结束日")
    strategies: Mapped[list[str]] = mapped_column(JsonType, nullable=False, doc="参与策略标识列表")
    params: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, doc="回测参数快照")
    report: Mapped[dict[str, Any] | None] = mapped_column(
        JsonType, nullable=True, doc="回测结果（A/B/C 三段）"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, doc="失败原因")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="开始时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="结束时间"
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, doc="触发者用户名")


class IngestJob(Base):
    """采集任务执行明细（幂等键 ``capability + args + trade_date``）。"""

    __tablename__ = "ingest_jobs"
    __table_args__ = (Index("ix_ingest_jobs_capability_trade_date", "capability", "trade_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False, doc="任务 ID"
    )
    capability: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, doc="数据源能力"
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, doc="数据源标识")
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True, doc="目标交易日")
    status: Mapped[str] = mapped_column(
        String(16), index=True, nullable=False, default="pending", doc="状态"
    )
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, doc="入库行数")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, doc="重试次数")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, doc="错误信息")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="开始时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="结束时间"
    )


class AgentSession(TimestampMixin, Base):
    """Agent 会话（右侧抽屉的多轮会话上下文）。"""

    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False, doc="会话 ID"
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, doc="所属用户名")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="会话标题")


class AgentMessage(Base):
    """Agent 会话消息（含模型回复与工具调用请求）。"""

    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, doc="会话 ID")
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, doc="角色：user/assistant/tool/system"
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True, doc="文本内容")
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JsonType, nullable=True, doc="模型请求的工具调用列表"
    )
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, doc="token 用量")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentToolCall(Base):
    """Agent 工具调用审计（入参、结果摘要、耗时、是否被拒）。"""

    __tablename__ = "agent_tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, doc="会话 ID")
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, doc="工具名")
    arguments: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, doc="调用入参")
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True, doc="结果摘要")
    ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(), doc="是否执行成功"
    )
    denied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), doc="是否因权限被拒"
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, doc="执行耗时（毫秒）")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "AdviceReport",
    "AgentMessage",
    "AgentSession",
    "AgentToolCall",
    "BacktestRun",
    "DragonPoolCandidate",
    "IngestJob",
]
