"""原始响应留档模型（``raw_*`` 语义）。

上游响应的原样落库，用于：字段映射排障、来源审计、快照回放取证。
保留策略：``raw_responses`` 保留 30 天，由采集侧清理任务执行。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JsonType


class RawResponse(Base):
    """上游原始响应（按 ``source + capability + args_hash`` 去重取最新）。"""

    __tablename__ = "raw_responses"
    __table_args__ = (
        Index(
            "ix_raw_responses_source_capability_trade_date",
            "source",
            "capability",
            "trade_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True, nullable=False, doc="数据源标识")
    capability: Mapped[str] = mapped_column(String(64), index=True, nullable=False, doc="能力标识")
    args_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="请求参数规范化后的哈希（幂等键组成部分）"
    )
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True, doc="目标交易日")
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, doc="原始响应载荷")
    sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="载荷内容哈希，用于变更检测"
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True, doc="HTTP 状态码")
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, doc="请求耗时（毫秒）")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        server_default=func.now(),
        nullable=False,
        doc="抓取时间",
    )


__all__ = ["RawResponse"]
