"""仓储层基类与通用查询原语。

本模块提供全仓储共用的四类能力：

- :class:`BaseRepository`：持有请求级 :class:`AsyncSession` 的所有仓储的基类。
- :class:`PageResult`：分页结果容器(``items`` / ``total`` / ``page`` / ``page_size`` / ``pages``)。
- :meth:`BaseRepository.paginate`：**强制有界**分页（页大小从配置读取，超限自动收敛到上限）。
- :meth:`BaseRepository.bulk_upsert`：按方言分派的**幂等覆盖写**原语，采集侧（Task 7）
  重复执行同一幂等键集合时 SHALL NOT 产生重复行。

方言分派说明：PostgreSQL 走 ``postgresql.insert().on_conflict_do_update``，
SQLite 走 ``sqlite.insert().on_conflict_do_update``，二者语义一致（均要求冲突目标
存在主键或唯一约束）；其他方言直接报错，避免静默降级为普通插入。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import ceil
from typing import Any, Generic, TypeVar

from sqlalchemy import ColumnElement, Select, delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT")
ItemT = TypeVar("ItemT")


@dataclass(frozen=True, slots=True)
class PageResult(Generic[ItemT]):
    """分页结果容器（禁止无界返回）。

    Attributes:
        items: 当前页记录。
        total: 满足条件的总记录数。
        page: 当前页码（从 1 起）。
        page_size: 实际生效的页大小（已按上限收敛）。
        pages: 总页数（``total`` 为 0 时为 0）。
    """

    items: list[ItemT]
    total: int
    page: int
    page_size: int
    pages: int


def _chunked(rows: Sequence[Mapping[str, Any]], size: int) -> Iterable[Sequence[Mapping[str, Any]]]:
    """把行列表按 ``size`` 切片，避免单条语句参数数量超限。"""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _insert_constructor(dialect_name: str) -> Any:
    """按方言返回带 ``on_conflict_do_update`` 的 insert 构造器。"""
    if dialect_name == "postgresql":
        return postgresql_insert
    if dialect_name == "sqlite":
        return sqlite_insert
    raise NotImplementedError(
        f"bulk_upsert 不支持的方言：{dialect_name!r}（仅支持 PostgreSQL / SQLite）"
    )


def _derive_update_columns(model: type[Any], conflict_columns: Sequence[str]) -> list[str]:
    """未显式给出更新列时，取除冲突列与主键列外的全部列。"""
    skip = set(conflict_columns)
    return [
        column.name
        for column in model.__table__.columns
        if column.name not in skip and not column.primary_key
    ]


class BaseRepository:
    """所有仓储的基类，持有请求级异步会话。

    Args:
        session: 请求级 :class:`AsyncSession`；仓储不自行开启/提交事务，
            事务边界由 :func:`app.db.session.get_session` 或调用方掌握。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ 方言

    def dialect_name(self) -> str:
        """返回当前会话绑定的方言名（``postgresql`` / ``sqlite``）。"""
        return str(self.session.get_bind().dialect.name)

    # ------------------------------------------------------------ 通用读取

    async def get_by_id(self, model: type[ModelT], pk: Any) -> ModelT | None:
        """按主键取单行，不存在返回 ``None``。"""
        return await self.session.get(model, pk)

    async def list_all(
        self,
        model: type[ModelT],
        filters: Mapping[str, Any] | None = None,
        *,
        order_by: Any = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ModelT]:
        """按等值过滤条件列出记录（可选排序/限量/偏移）。

        Args:
            model: ORM 模型类。
            filters: ``列名 -> 期望值`` 的等值过滤；空表示不过滤。
            order_by: 传给 ``ORDER BY`` 的列或表达式。
            limit: 返回行数上限；``None`` 表示不限制（调用方应自行保证有界）。
            offset: 跳过的行数。
        """
        stmt = select(model)
        if filters:
            stmt = stmt.where(*(getattr(model, key) == value for key, value in filters.items()))
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, stmt: Select[Any]) -> int:
        """统计任意 ``SELECT`` 语句的行数（剥离排序后包一层子查询）。"""
        subquery = stmt.order_by(None).subquery()
        total = await self.session.scalar(select(func.count()).select_from(subquery))
        return int(total or 0)

    async def delete_where(self, model: type[Any], *criteria: ColumnElement[bool]) -> int:
        """按条件批量删除，返回受影响行数。"""
        result = await self.session.execute(delete(model).where(*criteria))
        await self.session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    # -------------------------------------------------------------- 分页

    async def paginate(
        self,
        stmt: Select[Any],
        page: int = 1,
        page_size: int | None = None,
        *,
        settings: Settings | None = None,
    ) -> PageResult[Any]:
        """对任意 ``SELECT`` 语句分页，**强制页大小上限**。

        - ``page`` 最小为 1；``page_size`` 缺省取 ``settings.page_size_default``（20），
          并一律收敛到 ``[1, settings.page_size_max]``（200）区间内，杜绝无界返回。
        - 返回 :class:`PageResult`，其中 ``total`` / ``pages`` 基于完整结果集统计。

        Args:
            stmt: 已构造好的查询语句（可含 ``WHERE`` / ``ORDER BY``）。
            page: 页码，从 1 起。
            page_size: 期望页大小；``None`` 用默认值。
            settings: 显式配置（便于测试注入），缺省读全局配置。
        """
        resolved = settings or get_settings()
        max_size = max(1, resolved.page_size_max)
        default_size = min(max(1, resolved.page_size_default), max_size)
        size = default_size if page_size is None else min(max(1, page_size), max_size)
        number = max(1, page)

        total = await self.count(stmt)
        result = await self.session.execute(stmt.limit(size).offset((number - 1) * size))
        items = list(result.scalars().all())
        pages = ceil(total / size) if total > 0 else 0
        return PageResult(items=items, total=total, page=number, page_size=size, pages=pages)

    # ---------------------------------------------------------- 幂等覆盖写

    async def bulk_upsert(
        self,
        model: type[Any],
        rows: Sequence[Mapping[str, Any]],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str] | None = None,
        *,
        chunk_size: int | None = None,
    ) -> int:
        """按幂等键批量覆盖写（跨方言），返回处理行数。

        以 ``conflict_columns`` 为冲突目标执行 ``ON CONFLICT DO UPDATE``：
        键已存在则覆盖 ``update_columns`` 指定列，不存在则插入。**重复调用同一
        键集合不会产生重复行**，故可安全用于采集管道的重跑。

        Args:
            model: ORM 模型类。
            rows: 行字典序列；同一批内各行的键集合应保持一致。
            conflict_columns: 幂等键列（须对应主键或唯一约束）。
            update_columns: 冲突时要覆盖的列；``None`` 表示除冲突列与主键外的全部列。
            chunk_size: 每批行数；``None`` 读 ``settings.upsert_chunk_size``（500）。

        Raises:
            NotImplementedError: 方言非 PostgreSQL / SQLite。
        """
        materialized = list(rows)
        if not materialized:
            return 0

        resolved_chunk = chunk_size or get_settings().upsert_chunk_size
        resolved_update = (
            list(update_columns)
            if update_columns is not None
            else _derive_update_columns(model, conflict_columns)
        )
        insert = _insert_constructor(self.dialect_name())

        # DB 实际影响行数（插入 + 冲突更新）——与返回值 len(materialized) 可能不同
        # （重复键覆盖时 rowcount 只在部分驱动口径下累计）；排查「任务 rows=N 但
        # 表里查不到」时先看本日志（docs/bugs-2026-09-21.md Bug #2）。
        db_rowcount = 0

        for chunk in _chunked(materialized, max(1, resolved_chunk)):
            stmt = insert(model).values(list(chunk))
            if resolved_update:
                stmt = stmt.on_conflict_do_update(
                    index_elements=list(conflict_columns),
                    set_={name: stmt.excluded[name] for name in resolved_update},
                )
            else:  # pragma: no cover - 防御性分支：键即全部列时退化为 DO NOTHING
                stmt = stmt.on_conflict_do_nothing(index_elements=list(conflict_columns))
            result = await self.session.execute(stmt)
            db_rowcount += int(getattr(result, "rowcount", 0) or 0)

        await self.session.flush()
        if db_rowcount != len(materialized):
            logger.info(
                "bulk_upsert_rowcount",
                extra={
                    "table": getattr(model, "__tablename__", str(model)),
                    "submitted": len(materialized),
                    "db_rowcount": db_rowcount,
                },
            )
        return len(materialized)


__all__ = ["BaseRepository", "PageResult"]
