"""池/快照类仓储（``std_*`` 语义）：涨停池、池快照、情绪周期判定、连板天梯。

均继承 :class:`~app.repositories.base.BaseRepository`，对外暴露类型化查询方法。
写入语义偏「快照」：除幂等 upsert 外，还提供整批替换（先清后写）与跨表回填。
所有批量写入统一走 :meth:`BaseRepository.bulk_upsert`，保证采集重跑的幂等性。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, select, update

from app.models.market import (
    CycleJudgement,
    LadderRow,
    LimitUpPool,
    MinuteBar,
    PoolSnapshot,
)
from app.repositories.base import BaseRepository

__all__ = [
    "CycleJudgementRepository",
    "LadderRepository",
    "LimitUpPoolRepository",
    "PoolSnapshotRepository",
]

# 各表的幂等键与覆盖列（集中声明，便于审阅与复用）。
_LIMIT_UP_CONFLICT = ("trade_date", "pool_type", "code")
_LIMIT_UP_UPDATE = (
    "name",
    "continue_days",
    "limit_up_time",
    "seal_amount_yuan",
    "max_seal_amount_yuan",
    "open_times",
    "turnover_rate",
    "amount_yuan",
    "market_cap_yuan",
    "source",
)
_POOL_SNAPSHOT_CONFLICT = ("trade_date", "pool_name")
_POOL_SNAPSHOT_UPDATE = ("payload", "source")
_CYCLE_CONFLICT = ("trade_date",)
_CYCLE_UPDATE = (
    "state",
    "reasons",
    "indicators",
    "overheated",
    "relaxed_needs_confirm",
    "data_degraded",
    "position_factor",
    "ran_at",
    "source",
)
_LADDER_CONFLICT = ("trade_date", "code")
_LADDER_UPDATE = ("name", "continue_days", "first_seal_time", "source")


class LimitUpPoolRepository(BaseRepository):
    """涨停池仓储（含多种池型）。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """按 ``(trade_date, pool_type, code)`` 幂等覆盖写入涨停池。"""
        return await self.bulk_upsert(LimitUpPool, rows, _LIMIT_UP_CONFLICT, _LIMIT_UP_UPDATE)

    async def merge_supplement(
        self, trade_date: date, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]
    ) -> int:
        """**合并补数**：只更新既有行的指定列，不插入新行、不覆盖其他列。

        用于 hithink 第 8 轮补数场景：xuangutong 主源写入的当日涨停池行缺
        封单金额/最大封单金额，hithink 端点有这几个字段——按
        ``(trade_date, pool_type, code)`` 命中后仅回填 ``columns`` 列，主源的
        涨停原因/时间线/量比等字段不受影响。**未命中的行忽略**（hithink 池
        口径与主源略有出入属正常，不强行插入）。

        Args:
            trade_date: 目标交易日。
            rows: 补数行（须含冲突键列 + ``columns`` 列）。
            columns: 只回填这些列（如 ``seal_amount_yuan``）。

        Returns:
            实际更新的行数。
        """
        if not rows:
            return 0
        updated = 0
        for row in rows:
            stmt = (
                update(LimitUpPool)
                .where(
                    LimitUpPool.trade_date == trade_date,
                    LimitUpPool.pool_type == row["pool_type"],
                    LimitUpPool.code == row["code"],
                )
                .values(
                    **{col: row[col] for col in columns if row.get(col) is not None},
                    source=row.get("source"),
                )
            )
            result = await self.session.execute(stmt)
            updated += int(getattr(result, "rowcount", 0) or 0)
        return updated

    async def replace_pool(self, trade_date: date, rows: Sequence[Mapping[str, Any]]) -> int:
        """**整批替换**某交易日的涨停池快照（先清后写）。

        用于盘中轮询场景：库中只保留**最近一次拉取**的结果，而不是历次轮询的
        并集。否则「先涨停、后炸板」的票会因 ``upsert`` 只覆盖不删除而永久残留
        （``upsert_many`` 的冲突键是 ``(trade_date, pool_type, code)``，只更新
        已存在的行，删不掉本轮已不在池中的行）。

        语义与安全边界：

        - 只删除 ``rows`` 中实际出现的 ``pool_type``（同一交易日其他池型不受影响）；
        - ``rows`` 为空时**直接返回 0，不做任何删除**——空结果视为「本轮无数据」，
          避免上游瞬时异常/解析失败把当日已有快照清空。

        Args:
            trade_date: 目标交易日。
            rows: 本轮取到的池行（须含 ``pool_type`` 列）。

        Returns:
            写入行数（等于 ``len(rows)``；空输入为 0）。
        """
        materialized = list(rows)
        if not materialized:
            return 0
        pool_types = sorted(
            {str(row["pool_type"]) for row in materialized if row.get("pool_type") is not None}
        )
        if pool_types:
            await self.delete_where(
                LimitUpPool,
                LimitUpPool.trade_date == trade_date,
                LimitUpPool.pool_type.in_(pool_types),
            )
        return await self.upsert_many(materialized)

    async def get_pool(self, trade_date: date, pool_type: str) -> list[LimitUpPool]:
        """取某日某池型的成分，按连板天数降序。"""
        stmt = (
            select(LimitUpPool)
            .where(LimitUpPool.trade_date == trade_date, LimitUpPool.pool_type == pool_type)
            .order_by(LimitUpPool.continue_days.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_date(self, trade_date: date) -> list[LimitUpPool]:
        """取某日全部池型记录，按池型、连板天数降序。"""
        stmt = (
            select(LimitUpPool)
            .where(LimitUpPool.trade_date == trade_date)
            .order_by(LimitUpPool.pool_type, LimitUpPool.continue_days.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def aggregate_amount_from_minutes(self, trade_date: date) -> int:
        """**盘后聚合**：当日 minute_bars 的 amount_yuan 求和回填涨停池成交额。

        xuangutong 池端点不提供成交额；eltdx 分时（1m K 线）每分钟自带 amount。
        本方法在 SQL 端按 ``(code, trade_date)`` 聚合分钟成交额，回填到当日
        **全部池型**的 ``amount_yuan`` 列（同票多池型各自独立成行，均应回填）。

        只更新 ``amount_yuan IS NULL`` 或与聚合值不一致的行（幂等，重复执行无害）。
        分钟数据缺失的票不更新（保持 NULL，「能算的算、算不出的留空」）。

        Returns:
            实际更新的行数。
        """
        stmt = (
            update(LimitUpPool)
            .where(
                LimitUpPool.trade_date == trade_date,
                LimitUpPool.code.in_(
                    select(MinuteBar.code)
                    .where(
                        MinuteBar.trade_date == trade_date,
                        MinuteBar.amount_yuan.isnot(None),
                    )
                    .distinct()
                ),
            )
            .values(
                amount_yuan=func.coalesce(
                    select(func.sum(MinuteBar.amount_yuan))
                    .where(
                        MinuteBar.trade_date == trade_date,
                        MinuteBar.code == LimitUpPool.code,
                    )
                    .scalar_subquery(),
                    LimitUpPool.amount_yuan,
                )
            )
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    async def filter_by_continue_days(self, trade_date: date, min_days: int) -> list[LimitUpPool]:
        """取某日连板天数 ``>= min_days`` 的记录，按连板天数降序。"""
        stmt = (
            select(LimitUpPool)
            .where(
                LimitUpPool.trade_date == trade_date,
                LimitUpPool.continue_days >= min_days,
            )
            .order_by(LimitUpPool.continue_days.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def codes_between(
        self, start: date, end: date, pool_type: str = "limit_up"
    ) -> list[tuple[str, str]]:
        """取 ``[start, end]`` 区间内某池型的 ``(code, name)`` 去重列表（按代码升序）。

        供采集任务从「近期已入库的涨停池」推导策略相关标的（见 ``app.ingest.tasks``）。
        """
        stmt = (
            select(LimitUpPool.code, LimitUpPool.name)
            .where(
                LimitUpPool.trade_date.between(start, end),
                LimitUpPool.pool_type == pool_type,
            )
            .distinct()
            .order_by(LimitUpPool.code)
        )
        result = await self.session.execute(stmt)
        return [(code, name) for code, name in result.all()]

    async def get_pools_between(
        self, start: date, end: date, pool_types: Sequence[str]
    ) -> list[LimitUpPool]:
        """取 ``[start, end]`` 区间内多种池型的全部成分。

        供情绪周期历史装配（T-0008）一次拉齐分位窗口所需数据，避免逐日逐池
        循环查询。排序：交易日升序、池型升序、连板天数降序。
        """
        stmt = (
            select(LimitUpPool)
            .where(
                LimitUpPool.trade_date.between(start, end),
                LimitUpPool.pool_type.in_(list(pool_types)),
            )
            .order_by(
                LimitUpPool.trade_date,
                LimitUpPool.pool_type,
                LimitUpPool.continue_days.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest_trade_date(self) -> date | None:
        """库中最新交易日。"""
        value = await self.session.scalar(select(func.max(LimitUpPool.trade_date)))
        return value if isinstance(value, date) else None


class PoolSnapshotRepository(BaseRepository):
    """池快照原始载荷仓储。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """按 ``(trade_date, pool_name)`` 幂等覆盖写入池快照。"""
        return await self.bulk_upsert(
            PoolSnapshot, rows, _POOL_SNAPSHOT_CONFLICT, _POOL_SNAPSHOT_UPDATE
        )

    async def get(self, trade_date: date, pool_name: str) -> PoolSnapshot | None:
        """取某日某池名的快照载荷。"""
        stmt = select(PoolSnapshot).where(
            PoolSnapshot.trade_date == trade_date, PoolSnapshot.pool_name == pool_name
        )
        return await self.session.scalar(stmt)


class CycleJudgementRepository(BaseRepository):
    """情绪周期判定仓储（每交易日一行，**派生**数据）。"""

    async def upsert_one(self, row: Mapping[str, Any]) -> int:
        """按 ``trade_date`` 幂等覆盖写入判定（同日重跑只留最新一次）。"""
        return await self.bulk_upsert(CycleJudgement, [row], _CYCLE_CONFLICT, _CYCLE_UPDATE)

    async def get(self, trade_date: date) -> CycleJudgement | None:
        """取某交易日判定。"""
        stmt = select(CycleJudgement).where(CycleJudgement.trade_date == trade_date)
        return await self.session.scalar(stmt)

    async def latest(self) -> CycleJudgement | None:
        """取最新一条判定（供「昨日态 → relaxed_needs_confirm」比对）。"""
        stmt = select(CycleJudgement).order_by(CycleJudgement.trade_date.desc()).limit(1)
        return await self.session.scalar(stmt)

    async def previous_state(self, trade_date: date) -> str | None:
        """取 ``trade_date`` **之前**最近的周期态（无历史为 ``None``）。"""
        stmt = (
            select(CycleJudgement.state)
            .where(CycleJudgement.trade_date < trade_date)
            .order_by(CycleJudgement.trade_date.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)


class LadderRepository(BaseRepository):
    """连板天梯仓储。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """按 ``(trade_date, code)`` 幂等覆盖写入天梯。"""
        return await self.bulk_upsert(LadderRow, rows, _LADDER_CONFLICT, _LADDER_UPDATE)

    async def backfill_first_seal_from_pool(self, trade_date: date) -> int:
        """从当日涨停池回填天梯 ``first_seal_time``（按 code 对齐）。

        hithink ladder 端点不返回首次封板时间（实测仅 ``board_num`` /
        ``seal_nextday`` / ``sign_level``），该列曾为死映射恒 NULL。涨停池的
        ``limit_up_time``（xuangutong ``first_limit_up``，秒级时间戳）同日同票
        可用——本方法在 SQL 端按 ``(trade_date, code)`` 命中回填，仅更新
        ``first_seal_time IS NULL`` 的行（池数据后到时可再次补齐，幂等）。

        Returns:
            实际更新的行数。
        """
        stmt = (
            update(LadderRow)
            .where(
                LadderRow.trade_date == trade_date,
                LadderRow.first_seal_time.is_(None),
                LadderRow.code.in_(
                    select(LimitUpPool.code)
                    .where(
                        LimitUpPool.trade_date == trade_date,
                        LimitUpPool.limit_up_time.isnot(None),
                    )
                    .distinct()
                ),
            )
            .values(
                first_seal_time=select(LimitUpPool.limit_up_time)
                .where(
                    LimitUpPool.trade_date == trade_date,
                    LimitUpPool.code == LadderRow.code,
                    LimitUpPool.limit_up_time.isnot(None),
                )
                .limit(1)
                .scalar_subquery()
            )
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    async def get_range(
        self, start: date, end: date, min_continue_days: int = 2
    ) -> list[LadderRow]:
        """取 ``[start, end]`` 区间内连板天数达标的天梯记录。

        按交易日升序、同日内连板天数降序，便于前端按日分组渲染。
        """
        stmt = (
            select(LadderRow)
            .where(
                LadderRow.trade_date.between(start, end),
                LadderRow.continue_days >= min_continue_days,
            )
            .order_by(LadderRow.trade_date, LadderRow.continue_days.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def available_dates(self, limit: int = 30) -> list[date]:
        """可取的交易日列表（去重，倒序）。"""
        stmt = (
            select(LadderRow.trade_date)
            .distinct()
            .order_by(LadderRow.trade_date.desc())
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return [value for value in result.scalars().all() if isinstance(value, date)]
