"""行情类仓储（``std_*`` 语义）：股票、日线、分时、涨停池、快照、情绪、快讯、主题、监管、天梯。

一个聚合一个仓储，均继承 :class:`~app.repositories.base.BaseRepository`，对外暴露
类型化查询方法，供服务层与读 API（Task 12）调用。所有批量写入统一走
:meth:`BaseRepository.bulk_upsert`，保证采集重跑的幂等性。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select, update

from app.models.market import (
    CycleJudgement,
    DailyBar,
    LadderRow,
    LimitUpPool,
    MarketSentiment,
    MinuteBar,
    MonitorStock,
    NewsFlash,
    PoolSnapshot,
    Stock,
    Theme,
    ThemeStock,
)
from app.repositories.base import BaseRepository

# 各表的幂等键与覆盖列（集中声明，便于审阅与复用）。
_DAILY_BAR_CONFLICT = ("code", "trade_date")
_DAILY_BAR_UPDATE = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume_shares",
    "amount_yuan",
    "source",
)
_MINUTE_BAR_CONFLICT = ("code", "trade_date", "minute_index")
_MINUTE_BAR_UPDATE = ("time_label", "price", "volume_lots", "amount_yuan", "source")
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

_SENTIMENT_CONFLICT = ("trade_date",)
_SENTIMENT_UPDATE = (
    "temperature",
    "stage",
    "limit_up_count",
    "limit_down_count",
    "broken_board_count",
    "broken_rate",
    "up_count",
    "down_count",
    "max_continue_days",
    "premium_rate",
    "source",
)
_NEWS_CONFLICT = ("ts", "title")
_NEWS_UPDATE = ("summary", "symbols", "categories", "source")
_THEME_CONFLICT = ("trade_date", "name")
_THEME_UPDATE = ("rank", "core_avg_pct", "description", "core_count", "source")
_THEME_STOCK_CONFLICT = ("trade_date", "theme_name", "code")
_THEME_STOCK_UPDATE = (
    "name",
    "price",
    "pct",
    "turnover_rate",
    "continue_days",
    "source",
)
_MONITOR_CONFLICT = ("trade_date", "kind", "code")
_MONITOR_UPDATE = (
    "name",
    "reason",
    "start_date",
    "end_date",
    "notice_date",
    "info_code",
    "reason_type",
    "link_url",
    "source",
)
_LADDER_CONFLICT = ("trade_date", "code")
_LADDER_UPDATE = ("name", "continue_days", "first_seal_time", "source")
_STOCK_CONFLICT = ("code",)
_STOCK_UPDATE = ("name", "market", "board", "is_st", "list_date", "source")


class StockRepository(BaseRepository):
    """股票基础信息仓储。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """按 ``code`` 幂等覆盖写入股票基础信息。"""
        return await self.bulk_upsert(Stock, rows, _STOCK_CONFLICT, _STOCK_UPDATE)

    async def backfill_list_date(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """只回填 ``list_date`` 列（按 code 命中，不覆盖其他列、不插入新行）。

        上市日期来自 xuangutong 涨停池的 ``listed_date``（秒级时间戳），在池采集
        写入器里顺带补写——``stocks`` 行由 daily_bars 路径的
        ``_candidate_profiles`` 创建（无上市日期），此处仅补齐。
        """
        updated = 0
        for row in rows:
            code = row.get("code")
            list_date = row.get("list_date")
            if not code or list_date is None:
                continue
            stmt = (
                update(Stock)
                .where(Stock.code == code, Stock.list_date.is_(None))
                .values(list_date=list_date)
            )
            result = await self.session.execute(stmt)
            updated += int(getattr(result, "rowcount", 0) or 0)
        return updated

    async def get(self, code: str) -> Stock | None:
        """按证券代码取单只股票。"""
        return await self.session.get(Stock, code)

    async def list_all(  # type: ignore[override]
        self,
        *,
        market: str | None = None,
        board: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Stock]:
        """列出股票（可按市场/板块过滤），按代码升序。"""
        stmt = select(Stock)
        if market is not None:
            stmt = stmt.where(Stock.market == market)
        if board is not None:
            stmt = stmt.where(Stock.board == board)
        stmt = stmt.order_by(Stock.code)
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search(self, keyword: str) -> list[Stock]:
        """按代码或简称模糊搜索（最多 50 条）。"""
        pattern = f"%{keyword}%"
        stmt = (
            select(Stock)
            .where(or_(Stock.code.ilike(pattern), Stock.name.ilike(pattern)))
            .order_by(Stock.code)
            .limit(50)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class DailyBarRepository(BaseRepository):
    """日线行情仓储。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """按 ``(code, trade_date)`` 幂等覆盖写入日线。"""
        return await self.bulk_upsert(DailyBar, rows, _DAILY_BAR_CONFLICT, _DAILY_BAR_UPDATE)

    async def get_range(self, code: str, start: date, end: date) -> list[DailyBar]:
        """取某只股票 ``[start, end]`` 区间的日线，按交易日升序。"""
        stmt = (
            select(DailyBar)
            .where(DailyBar.code == code, DailyBar.trade_date.between(start, end))
            .order_by(DailyBar.trade_date)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest(self, code: str) -> DailyBar | None:
        """取某只股票最新一根日线。"""
        stmt = (
            select(DailyBar)
            .where(DailyBar.code == code)
            .order_by(DailyBar.trade_date.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def get_by_date(
        self, trade_date: date, codes: Sequence[str] | None = None
    ) -> list[DailyBar]:
        """取某交易日的日线；``codes`` 非空时只取指定代码集合。"""
        stmt = select(DailyBar).where(DailyBar.trade_date == trade_date)
        if codes:
            stmt = stmt.where(DailyBar.code.in_(list(codes)))
        stmt = stmt.order_by(DailyBar.code)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest_trade_date(self) -> date | None:
        """库中最新交易日。"""
        value = await self.session.scalar(select(func.max(DailyBar.trade_date)))
        return value if isinstance(value, date) else None


class MinuteBarRepository(BaseRepository):
    """分时行情仓储。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """按 ``(code, trade_date, minute_index)`` 幂等覆盖写入分时。"""
        return await self.bulk_upsert(MinuteBar, rows, _MINUTE_BAR_CONFLICT, _MINUTE_BAR_UPDATE)

    async def get_day(self, code: str, trade_date: date) -> list[MinuteBar]:
        """取某股某日的全部分时，按分钟序号升序。"""
        stmt = (
            select(MinuteBar)
            .where(MinuteBar.code == code, MinuteBar.trade_date == trade_date)
            .order_by(MinuteBar.minute_index)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_range(self, code: str, start: date, end: date) -> list[MinuteBar]:
        """取某股 ``[start, end]`` 区间的全部分时，按日期、分钟序号升序。"""
        stmt = (
            select(MinuteBar)
            .where(
                MinuteBar.code == code,
                MinuteBar.trade_date >= start,
                MinuteBar.trade_date <= end,
            )
            .order_by(MinuteBar.trade_date, MinuteBar.minute_index)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_day_bulk(self, codes: Sequence[str], trade_date: date) -> list[MinuteBar]:
        """一次取多只股票某日的分时，按代码、分钟序号升序。"""
        if not codes:
            return []
        stmt = (
            select(MinuteBar)
            .where(MinuteBar.code.in_(list(codes)), MinuteBar.trade_date == trade_date)
            .order_by(MinuteBar.code, MinuteBar.minute_index)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


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
        key_cols = ("trade_date", "pool_type", "code")
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


class MarketSentimentRepository(BaseRepository):
    """市场情绪仓储（每交易日一行）。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """按 ``trade_date`` 幂等覆盖写入情绪指标。"""
        return await self.bulk_upsert(MarketSentiment, rows, _SENTIMENT_CONFLICT, _SENTIMENT_UPDATE)

    async def get(self, trade_date: date) -> MarketSentiment | None:
        """取某交易日情绪指标。"""
        stmt = select(MarketSentiment).where(MarketSentiment.trade_date == trade_date)
        return await self.session.scalar(stmt)

    async def get_history(self, days: int) -> list[MarketSentiment]:
        """取最近 ``days`` 个交易日的情绪，**按交易日升序**（供情绪走势图）。"""
        stmt = (
            select(MarketSentiment).order_by(MarketSentiment.trade_date.desc()).limit(max(1, days))
        )
        result = await self.session.execute(stmt)
        return list(reversed(list(result.scalars().all())))

    async def latest(self) -> MarketSentiment | None:
        """取最新一条情绪指标。"""
        stmt = select(MarketSentiment).order_by(MarketSentiment.trade_date.desc()).limit(1)
        return await self.session.scalar(stmt)


class NewsFlashRepository(BaseRepository):
    """快讯仓储。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """按 ``(ts, title)`` 幂等覆盖写入快讯。"""
        return await self.bulk_upsert(NewsFlash, rows, _NEWS_CONFLICT, _NEWS_UPDATE)

    async def list_recent(self, limit: int = 50, keyword: str | None = None) -> list[NewsFlash]:
        """按发布时间倒序取快讯。

        Args:
            limit: 返回条数上限。
            keyword: 关键词，命中标题**或**摘要即返回。
        """
        stmt = select(NewsFlash)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(or_(NewsFlash.title.ilike(pattern), NewsFlash.summary.ilike(pattern)))
        stmt = stmt.order_by(NewsFlash.ts.desc()).limit(max(1, limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_between(self, start_ts: Any, end_ts: Any) -> list[NewsFlash]:
        """取发布时间落在 ``[start_ts, end_ts]`` 的快讯，按时间升序。"""
        stmt = (
            select(NewsFlash).where(NewsFlash.ts.between(start_ts, end_ts)).order_by(NewsFlash.ts)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class ThemeRepository(BaseRepository):
    """主题（板块）仓储，含主题成分股。"""

    async def upsert_many(
        self,
        themes: Sequence[Mapping[str, Any]],
        stocks: Sequence[Mapping[str, Any]] | None = None,
    ) -> int:
        """幂等覆盖写入主题榜与其成分股，返回写入总行数。

        主题按 ``(trade_date, name)``、成分股按 ``(trade_date, theme_name, code)`` 去重。
        """
        total = await self.bulk_upsert(Theme, themes, _THEME_CONFLICT, _THEME_UPDATE)
        if stocks:
            total += await self.bulk_upsert(
                ThemeStock, stocks, _THEME_STOCK_CONFLICT, _THEME_STOCK_UPDATE
            )
        return total

    async def replace_themes(
        self,
        trade_date: date,
        themes: Sequence[Mapping[str, Any]] | None = None,
        stocks: Sequence[Mapping[str, Any]] | None = None,
    ) -> int:
        """**整批替换**某日的主题榜 / 成分股（先清后写），返回写入行数。

        用于盘中轮询场景：库中只保留**最近一次拉取**的结果。榜单是完整快照，
        而 ``upsert_many`` 只覆盖已存在的行——盘中榜单会滚动变化，早先入榜、
        之后掉出的题材名会永久残留（实测当日累积到 44 行 / rank 到 42，而当前
        榜单只有 24 个），因此必须替换而非合并。

        语义与安全边界：

        - ``themes`` / ``stocks`` 传入 ``None`` 或**空序列时不触发删除**
          （空结果视为「本轮无数据」，避免上游瞬时异常把当日已有快照清空）；
        - 两个参数相互独立，可只替换其中一个。

        Args:
            trade_date: 目标交易日。
            themes: 本轮取到的题材榜行；空则不动 ``themes`` 表。
            stocks: 本轮取到的题材成分股行；空则不动 ``theme_stocks`` 表。

        Returns:
            写入行数之和。
        """
        total = 0
        if themes:
            await self.delete_where(Theme, Theme.trade_date == trade_date)
            total += await self.bulk_upsert(Theme, themes, _THEME_CONFLICT, _THEME_UPDATE)
        if stocks:
            await self.delete_where(ThemeStock, ThemeStock.trade_date == trade_date)
            total += await self.bulk_upsert(
                ThemeStock, stocks, _THEME_STOCK_CONFLICT, _THEME_STOCK_UPDATE
            )
        return total

    async def set_core_counts(self, trade_date: date, counts: Mapping[str, int]) -> int:
        """按题材名回填某日的 ``core_count``（核心股数量），返回更新行数。

        上游 ``plate/data`` 不提供核心股数量（请求该字段恒为 ``null``），故数量由
        ``theme_stocks`` 聚合得到——写入成分股之后调用即可。
        """
        if not counts:
            return 0
        updated = 0
        for name, count in counts.items():
            result = await self.session.execute(
                update(Theme)
                .where(Theme.trade_date == trade_date, Theme.name == name)
                .values(core_count=int(count))
            )
            updated += int(getattr(result, "rowcount", 0) or 0)
        await self.session.flush()
        return updated

    async def get_by_date(self, trade_date: date) -> list[Theme]:
        """取某日主题强度榜，按排名升序。"""
        stmt = select(Theme).where(Theme.trade_date == trade_date).order_by(Theme.rank)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_stocks(self, trade_date: date, theme_name: str) -> list[ThemeStock]:
        """取某日某主题的成分股，按当日涨幅降序。"""
        stmt = (
            select(ThemeStock)
            .where(ThemeStock.trade_date == trade_date, ThemeStock.theme_name == theme_name)
            .order_by(ThemeStock.pct.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def available_dates(self, limit: int = 30) -> list[date]:
        """可取的主题交易日列表（去重，倒序），供历史下拉。"""
        stmt = (
            select(Theme.trade_date)
            .distinct()
            .order_by(Theme.trade_date.desc())
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return [value for value in result.scalars().all() if isinstance(value, date)]


class MonitorStockRepository(BaseRepository):
    """监管名单仓储。"""

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """按 ``(trade_date, kind, code)`` 幂等覆盖写入监管名单。"""
        return await self.bulk_upsert(MonitorStock, rows, _MONITOR_CONFLICT, _MONITOR_UPDATE)

    async def get_by_date(self, trade_date: date, kind: str | None = None) -> list[MonitorStock]:
        """取某日监管名单，可按类型过滤，按代码升序。"""
        stmt = select(MonitorStock).where(MonitorStock.trade_date == trade_date)
        if kind is not None:
            stmt = stmt.where(MonitorStock.kind == kind)
        stmt = stmt.order_by(MonitorStock.code)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


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


__all__ = [
    "DailyBarRepository",
    "LadderRepository",
    "LimitUpPoolRepository",
    "MarketSentimentRepository",
    "MinuteBarRepository",
    "MonitorStockRepository",
    "NewsFlashRepository",
    "PoolSnapshotRepository",
    "StockRepository",
    "ThemeRepository",
]
