"""读路径附加只读仓储（Task 12 新增，纯附加、不改动既有仓储）。

既有仓储已覆盖绝大多数查询，但下列能力缺失（**报告为缺口，不修改既有文件**）：

1. ``stocks`` / ``news_flash`` / ``ladder_rows`` 的**分页**查询（既有方法返回无界
   ``list`` 或仅接受 ``limit``）；
2. 行情表的**新鲜度**信息（``max(ingested_at)`` / ``max(trade_date)``），供读接口
   计算 ``stale`` 标记。

本模块只读、不写，是服务层「唯一 DB 出口」的一部分；所有方法均返回类型化结果，
分页一律经 :meth:`BaseRepository.paginate`（强制页大小上限）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, or_, select

from app.models.market import LadderRow, NewsFlash, Stock
from app.repositories.base import BaseRepository, PageResult

__all__ = ["ReadRepository"]


class ReadRepository(BaseRepository):
    """读接口所需的附加只读查询。"""

    async def paginate_stocks(
        self, *, keyword: str | None = None, page: int = 1, page_size: int | None = None
    ) -> PageResult[Any]:
        """按代码/简称关键词分页检索股票（无关键词时列出全部，按代码升序）。"""
        stmt = select(Stock)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(or_(Stock.code.ilike(pattern), Stock.name.ilike(pattern)))
        stmt = stmt.order_by(Stock.code)
        return await self.paginate(stmt, page, page_size)

    async def paginate_news(
        self,
        *,
        level: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int | None = None,
    ) -> PageResult[Any]:
        """按重要级别/关键词分页检索快讯（按发布时间倒序）。"""
        stmt = select(NewsFlash)
        if level:
            stmt = stmt.where(NewsFlash.level == level)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(
                or_(NewsFlash.title.ilike(pattern), NewsFlash.summary.ilike(pattern))
            )
        stmt = stmt.order_by(NewsFlash.ts.desc())
        return await self.paginate(stmt, page, page_size)

    async def paginate_ladder(
        self,
        *,
        start: date,
        end: date,
        min_continue_days: int,
        page: int = 1,
        page_size: int | None = None,
    ) -> PageResult[Any]:
        """分页取区间内连板天数达标的天梯记录（按交易日、连板天数降序）。"""
        stmt = (
            select(LadderRow)
            .where(
                LadderRow.trade_date.between(start, end),
                LadderRow.continue_days >= min_continue_days,
            )
            .order_by(LadderRow.trade_date, LadderRow.continue_days.desc())
        )
        return await self.paginate(stmt, page, page_size)

    async def latest_ingested_at(self, model: type[Any]) -> datetime | None:
        """某表最近一次入库时间（用于 ``stale`` 判定）；无数据返回 ``None``。"""
        value = await self.session.scalar(select(func.max(model.ingested_at)))
        return value if isinstance(value, datetime) else None

    async def latest_date(self, model: type[Any], column: str) -> date | None:
        """某表某日期列的最大值（库中最新数据日期）；无数据返回 ``None``。"""
        value = await self.session.scalar(select(func.max(getattr(model, column))))
        return value if isinstance(value, date) else None
