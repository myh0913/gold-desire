"""通用响应契约：强制有界分页容器与行情数据新鲜度标记。

- :class:`PageResponse`：所有列表接口的统一分页外壳（禁止无界返回）。
- :class:`MarketMeta` / :class:`MarketPageResponse`：行情类响应携带 ``stale`` 与
  ``data_date``，用于「上游不可用时仍返回库中数据并标记新鲜度」（spec 读 API 要求）。

数值字段一律声明为 ``float``（API 边界口径），由 Pydantic 从 ORM 的 ``Decimal``
自动转换，保证缓存 JSON 往返无损。
"""

from __future__ import annotations

from datetime import date
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

__all__ = [
    "DatesResponse",
    "MarketMeta",
    "MarketPageResponse",
    "MessageResponse",
    "PageResponse",
]

T = TypeVar("T")


class PageResponse(BaseModel, Generic[T]):
    """通用分页响应（``page_size`` 为**实际生效**值，已按上限收敛）。"""

    items: list[T] = Field(default_factory=list, description="当前页记录")
    total: int = Field(description="满足条件的总记录数")
    page: int = Field(description="当前页码（从 1 起）")
    page_size: int = Field(description="实际生效页大小")
    pages: int = Field(description="总页数")


class MarketMeta(BaseModel):
    """行情数据新鲜度标记。"""

    stale: bool = Field(default=False, description="数据是否已超出预期新鲜度窗口")
    data_date: date | None = Field(default=None, description="库中最新数据日期")


class MarketPageResponse(BaseModel, Generic[T]):
    """带新鲜度标记的分页响应（行情列表用）。"""

    items: list[T] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    pages: int
    stale: bool = False
    data_date: date | None = None


class DatesResponse(BaseModel):
    """可用交易日列表（去重倒序，条数受 ``limit`` 约束）。"""

    dates: list[date] = Field(default_factory=list)
    limit: int = Field(description="实际生效的条数上限")


class MessageResponse(BaseModel):
    """简单操作结果。"""

    ok: bool = True
    message: str | None = None
