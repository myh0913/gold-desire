"""SQLAlchemy 声明式基类、跨方言列类型与公共列 Mixin。

- :class:`Base`：本项目所有 ORM 模型的声明基类。
- :data:`JsonType`：跨方言 JSON 列类型（PostgreSQL 落 ``JSONB``，SQLite 落 ``JSON``）。
- :class:`TimestampMixin` / :class:`SourceMixin`：可复用的公共列。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(postgresql.JSONB(), "postgresql")
"""JSON 列类型：PostgreSQL 使用 ``JSONB``（可索引/可查询），其余方言使用 ``JSON``。"""


class Base(DeclarativeBase):
    """本项目所有 ORM 模型的声明基类。"""

    def __repr__(self) -> str:
        """以主键为核心的紧凑表示。"""
        table = type(self).__name__
        pk = getattr(self, "id", None)
        return f"<{table} id={pk!r}>"


class TimestampMixin:
    """创建/更新时间公共列（数据库侧默认值，避免依赖应用时钟）。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SourceMixin:
    """数据来源与入库时间公共列（行情/原始数据表统一携带，便于溯源与重放）。"""

    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", doc="数据来源标识，如 xuangutong/eastmoney"
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, doc="入库时间"
    )


__all__ = ["Base", "JsonType", "SourceMixin", "TimestampMixin"]
