"""行情读服务：只读 DB + 两级缓存，**绝不触达上游、绝不扫描文件系统**。

职责边界（spec「读 API 与高并发」）：

- 只调用 :class:`~app.repositories.reads.ReadRepository` 与既有仓储；
- 拥有缓存键、TTL 与失效策略（委托 :class:`~app.services.cache_policy.CachePolicy`）；
- 计算 ``stale`` / ``data_date``（见 :mod:`app.services.freshness`）。

所有方法返回 Pydantic 响应模型，便于缓存 JSON 往返与 OpenAPI 类型化。
"""

from __future__ import annotations

from datetime import date

from app.core.errors import NotFoundError
from app.models.market import (
    CycleJudgement,
    DailyBar,
    LadderRow,
    LimitUpPool,
    MarketSentiment,
    MinuteBar,
    MonitorStock,
    NewsFlash,
    Theme,
)
from app.repositories import Repositories
from app.repositories.reads import ReadRepository
from app.schemas.common import DatesResponse, MarketPageResponse, PageResponse
from app.schemas.market import (
    CycleOut,
    CycleResponse,
    DailyBarOut,
    DailyBarsResponse,
    LadderCellOut,
    LadderMatrixResponse,
    LadderMatrixRowOut,
    LadderRowOut,
    LimitUpPoolOut,
    MinuteBarOut,
    MinuteBarsResponse,
    MonitorResponse,
    MonitorStockOut,
    NewsFlashOut,
    PoolResponse,
    PoolsResponse,
    SentimentHistoryResponse,
    SentimentOut,
    SentimentResponse,
    StockOut,
    ThemeOut,
    ThemesResponse,
    ThemeStockOut,
    ThemeStocksResponse,
)
from app.services.cache_policy import CachePolicy, get_cache_policy, query_key
from app.services.freshness import market_freshness
from app.services.paging import clamp_limit, page_fields

__all__ = ["MarketService"]


def _iso(value: date | None) -> str | None:
    """日期转 ISO 串；``None`` 原样返回（供缓存键拼装）。"""
    return value.isoformat() if value is not None else None


class MarketService:
    """行情读服务。"""

    def __init__(self, repos: Repositories, policy: CachePolicy | None = None) -> None:
        self._repos = repos
        self._read = ReadRepository(repos.session)
        self._policy = policy if policy is not None else get_cache_policy()

    # ------------------------------------------------------------------ 股票

    async def stocks(
        self, *, keyword: str | None, page: int, page_size: int | None
    ) -> PageResponse[StockOut]:
        """分页检索股票（``keyword`` 命中代码或简称）。"""
        key = query_key("stock", {"view": "list", "kw": keyword, "page": page, "size": page_size})

        async def loader() -> PageResponse[StockOut]:
            result = await self._read.paginate_stocks(
                keyword=keyword, page=page, page_size=page_size
            )
            return PageResponse[StockOut](
                items=[StockOut.model_validate(row) for row in result.items],
                **page_fields(result),
            )

        return await self._policy.get_or_load(
            "stock", key, loader, PageResponse[StockOut].model_validate
        )

    async def stock(self, code: str) -> StockOut:
        """取单只股票基础信息（不存在 404）。"""
        key = query_key("stock", {"view": "one", "code": code})

        async def loader() -> StockOut:
            row = await self._repos.stocks.get(code)
            if row is None:
                raise NotFoundError(f"股票不存在：{code}", detail={"code": code})
            return StockOut.model_validate(row)

        return await self._policy.get_or_load("stock", key, loader, StockOut.model_validate)

    # ------------------------------------------------------------ 日线 / 分时

    async def daily_bars(self, *, code: str, start: date, end: date) -> DailyBarsResponse:
        """取某只股票 ``[start, end]`` 区间日线。"""
        key = query_key(
            "daily_bars", {"code": code, "start": _iso(start), "end": _iso(end)}
        )

        async def loader() -> DailyBarsResponse:
            rows = await self._repos.daily_bars.get_range(code, start, end)
            stale, data_date = await market_freshness(self._read, DailyBar)
            return DailyBarsResponse(
                code=code,
                start=start,
                end=end,
                items=[DailyBarOut.model_validate(row) for row in rows],
                stale=stale,
                data_date=data_date,
            )

        return await self._policy.get_or_load(
            "daily_bars", key, loader, DailyBarsResponse.model_validate
        )

    async def minute_bars(self, *, code: str, on_date: date) -> MinuteBarsResponse:
        """取某只股票某日全部分时。"""
        key = query_key("minute_bars", {"code": code, "date": _iso(on_date)})

        async def loader() -> MinuteBarsResponse:
            rows = await self._repos.minute_bars.get_day(code, on_date)
            stale, data_date = await market_freshness(self._read, MinuteBar)
            return MinuteBarsResponse(
                code=code,
                trade_date=on_date,
                items=[MinuteBarOut.model_validate(row) for row in rows],
                stale=stale,
                data_date=data_date,
            )

        return await self._policy.get_or_load(
            "minute_bars", key, loader, MinuteBarsResponse.model_validate
        )

    # ------------------------------------------------------------------ 涨停池

    async def pools(self, on_date: date | None) -> PoolsResponse:
        """取某日全部池型（``date`` 缺省用库中最新交易日）。"""
        key = query_key("pool", {"view": "all", "date": _iso(on_date)})

        async def loader() -> PoolsResponse:
            target = on_date or await self._repos.limit_up_pool.latest_trade_date()
            rows = await self._repos.limit_up_pool.get_by_date(target) if target else []
            grouped: dict[str, list[LimitUpPoolOut]] = {}
            for row in rows:
                grouped.setdefault(row.pool_type, []).append(LimitUpPoolOut.model_validate(row))
            stale, data_date = await market_freshness(self._read, LimitUpPool)
            return PoolsResponse(
                trade_date=target, pools=grouped, stale=stale, data_date=data_date
            )

        return await self._policy.get_or_load("pool", key, loader, PoolsResponse.model_validate)

    async def pool(
        self, *, pool_type: str, on_date: date | None, min_continue_days: int
    ) -> PoolResponse:
        """取某日某池型成分（``min_continue_days`` 为连板天数下界）。"""
        key = query_key(
            "pool",
            {"view": pool_type, "date": _iso(on_date), "min_days": min_continue_days},
        )

        async def loader() -> PoolResponse:
            target = on_date or await self._repos.limit_up_pool.latest_trade_date()
            rows = (
                await self._repos.limit_up_pool.get_pool(target, pool_type) if target else []
            )
            kept = [row for row in rows if row.continue_days >= min_continue_days]
            stale, data_date = await market_freshness(self._read, LimitUpPool)
            return PoolResponse(
                trade_date=target,
                pool_type=pool_type,
                min_continue_days=min_continue_days,
                items=[LimitUpPoolOut.model_validate(row) for row in kept],
                stale=stale,
                data_date=data_date,
            )

        return await self._policy.get_or_load("pool", key, loader, PoolResponse.model_validate)

    # ------------------------------------------------------------------ 天梯

    async def ladder(
        self,
        *,
        start: date,
        end: date,
        min_continue_days: int,
        page: int,
        page_size: int | None,
    ) -> MarketPageResponse[LadderRowOut]:
        """分页取区间内连板天数达标的天梯记录。"""
        key = query_key(
            "ladder",
            {
                "start": _iso(start),
                "end": _iso(end),
                "min_days": min_continue_days,
                "page": page,
                "size": page_size,
            },
        )

        async def loader() -> MarketPageResponse[LadderRowOut]:
            result = await self._read.paginate_ladder(
                start=start,
                end=end,
                min_continue_days=min_continue_days,
                page=page,
                page_size=page_size,
            )
            stale, data_date = await market_freshness(self._read, LadderRow)
            return MarketPageResponse[LadderRowOut](
                items=[LadderRowOut.model_validate(row) for row in result.items],
                stale=stale,
                data_date=data_date,
                **page_fields(result),
            )

        return await self._policy.get_or_load(
            "ladder", key, loader, MarketPageResponse[LadderRowOut].model_validate
        )

    async def ladder_matrix(
        self, *, start: date | None, end: date | None, min_continue_days: int, limit_days: int
    ) -> LadderMatrixResponse:
        """取连板天梯**矩阵**（列=交易日升序，行=个股，单元格=连板数 + 首封时间）。

        与 :meth:`ladder`（平铺分页）并列：矩阵口径一次返回整个区间的聚合结果，
        供前端「Excel 式」列布局渲染。区间缺省取库中最近 ``limit_days`` 个交易日。

        Args:
            start: 起始交易日（含）；``None`` 表示按 ``limit_days`` 自动取。
            end: 结束交易日（含）；``None`` 表示取到最新。
            min_continue_days: 连板天数下界（缺省 2，与参考实现一致）。
            limit_days: 未指定区间时取的交易日个数。
        """
        key = query_key(
            "ladder",
            {
                "view": "matrix",
                "start": _iso(start),
                "end": _iso(end),
                "min_days": min_continue_days,
                "limit_days": limit_days,
            },
        )

        async def loader() -> LadderMatrixResponse:
            available = await self._repos.ladder.available_dates(limit=366)
            if start is not None and end is not None:
                window_start, window_end = start, end
            elif available:
                # available 为倒序；取最近 limit_days 个交易日作为默认窗口
                recent = available[: max(1, limit_days)]
                window_start, window_end = min(recent), max(recent)
                if start is not None:
                    window_start = start
                if end is not None:
                    window_end = end
            else:
                window_start, window_end = date.min, date.max

            rows = await self._repos.ladder.get_range(
                window_start, window_end, min_continue_days=min_continue_days
            )

            days: list[str] = sorted({row.trade_date.isoformat() for row in rows})
            grouped: dict[str, LadderMatrixRowOut] = {}
            for row in rows:
                entry = grouped.get(row.code)
                if entry is None:
                    entry = LadderMatrixRowOut(code=row.code, name=row.name)
                    grouped[row.code] = entry
                entry.cells[row.trade_date.isoformat()] = LadderCellOut(
                    boards=row.continue_days, first_seal_time=row.first_seal_time
                )

            stale, data_date = await market_freshness(self._read, LadderRow)
            return LadderMatrixResponse(
                days=days,
                rows=sorted(grouped.values(), key=lambda item: item.code),
                min_continue_days=min_continue_days,
                stale=stale,
                data_date=data_date,
            )

        return await self._policy.get_or_load(
            "ladder", key, loader, LadderMatrixResponse.model_validate
        )

    async def ladder_dates(self, limit: int | None) -> DatesResponse:
        """可取的天梯交易日列表（去重倒序）。"""
        effective = clamp_limit(limit, default=30)
        key = query_key("ladder", {"view": "dates", "limit": effective})

        async def loader() -> DatesResponse:
            dates = await self._repos.ladder.available_dates(effective)
            return DatesResponse(dates=dates, limit=effective)

        return await self._policy.get_or_load(
            "ladder", key, loader, DatesResponse.model_validate
        )

    # ------------------------------------------------------------------ 情绪

    async def cycle(self, on_date: date | None) -> CycleResponse:
        """取某交易日情绪周期判定（缺省取库中最新）。

        判定是**派生**数据（由情绪指标 + 涨停池算出并落库），随情绪采集任务同节拍
        刷新（交易时段每 10 分钟）。库中尚无判定时 ``item`` 为 ``null``。
        """
        stale, data_date = await market_freshness(self._read, CycleJudgement)
        row = (
            await self._repos.cycle_judgements.get(on_date)
            if on_date is not None
            else await self._repos.cycle_judgements.latest()
        )
        if row is None:
            return CycleResponse(stale=True, data_date=data_date)
        return CycleResponse(
            stale=stale,
            data_date=row.trade_date,
            trade_date=row.trade_date,
            item=CycleOut.model_validate(row),
        )

    async def sentiment(self, on_date: date | None) -> SentimentResponse:
        """取某交易日情绪指标（``date`` 缺省用库中最新）。"""
        key = query_key("sentiment_live", {"date": _iso(on_date)})

        async def loader() -> SentimentResponse:
            target = on_date
            if target is None:
                latest = await self._repos.market_sentiment.latest()
                target = latest.trade_date if latest is not None else None
            row = await self._repos.market_sentiment.get(target) if target else None
            stale, data_date = await market_freshness(self._read, MarketSentiment)
            return SentimentResponse(
                trade_date=target,
                item=SentimentOut.model_validate(row) if row is not None else None,
                stale=stale,
                data_date=data_date,
            )

        return await self._policy.get_or_load(
            "sentiment_live", key, loader, SentimentResponse.model_validate
        )

    async def sentiment_history(self, days: int | None) -> SentimentHistoryResponse:
        """取最近 ``days`` 个交易日情绪（升序）。"""
        effective = clamp_limit(days, default=20)
        key = query_key("sentiment_history", {"days": effective})

        async def loader() -> SentimentHistoryResponse:
            rows = await self._repos.market_sentiment.get_history(effective)
            stale, data_date = await market_freshness(self._read, MarketSentiment)
            return SentimentHistoryResponse(
                days=effective,
                items=[SentimentOut.model_validate(row) for row in rows],
                stale=stale,
                data_date=data_date,
            )

        return await self._policy.get_or_load(
            "sentiment_history", key, loader, SentimentHistoryResponse.model_validate
        )

    # ------------------------------------------------------------------ 主题

    async def themes(self, on_date: date | None) -> ThemesResponse:
        """取某交易日主题强度榜（``date`` 缺省用库中最新）。"""
        key = query_key("theme", {"view": "rank", "date": _iso(on_date)})

        async def loader() -> ThemesResponse:
            target = on_date
            if target is None:
                available = await self._repos.themes.available_dates(1)
                target = available[0] if available else None
            rows = await self._repos.themes.get_by_date(target) if target else []
            stale, data_date = await market_freshness(self._read, Theme)
            return ThemesResponse(
                trade_date=target,
                items=[ThemeOut.model_validate(row) for row in rows],
                stale=stale,
                data_date=data_date,
            )

        return await self._policy.get_or_load("theme", key, loader, ThemesResponse.model_validate)

    async def theme_dates(self, limit: int | None) -> DatesResponse:
        """可取的主题交易日列表（去重倒序）。"""
        effective = clamp_limit(limit, default=30)
        key = query_key("theme", {"view": "dates", "limit": effective})

        async def loader() -> DatesResponse:
            dates = await self._repos.themes.available_dates(effective)
            return DatesResponse(dates=dates, limit=effective)

        return await self._policy.get_or_load("theme", key, loader, DatesResponse.model_validate)

    async def theme_stocks(self, *, on_date: date, theme_name: str) -> ThemeStocksResponse:
        """取某日某主题的成分股。"""
        key = query_key("theme", {"view": "stocks", "date": _iso(on_date), "name": theme_name})

        async def loader() -> ThemeStocksResponse:
            rows = await self._repos.themes.get_stocks(on_date, theme_name)
            stale, data_date = await market_freshness(self._read, Theme)
            return ThemeStocksResponse(
                trade_date=on_date,
                theme_name=theme_name,
                items=[ThemeStockOut.model_validate(row) for row in rows],
                stale=stale,
                data_date=data_date,
            )

        return await self._policy.get_or_load(
            "theme", key, loader, ThemeStocksResponse.model_validate
        )

    # --------------------------------------------------------------- 快讯 / 监管

    async def newsflash(
        self,
        *,
        level: str | None,
        keyword: str | None,
        page: int,
        page_size: int | None,
    ) -> MarketPageResponse[NewsFlashOut]:
        """分页检索快讯（按发布时间倒序）。"""
        key = query_key(
            "newsflash",
            {"level": level, "kw": keyword, "page": page, "size": page_size},
        )

        async def loader() -> MarketPageResponse[NewsFlashOut]:
            result = await self._read.paginate_news(
                level=level, keyword=keyword, page=page, page_size=page_size
            )
            stale, data_date = await market_freshness(self._read, NewsFlash, date_column=None)
            return MarketPageResponse[NewsFlashOut](
                items=[NewsFlashOut.model_validate(row) for row in result.items],
                stale=stale,
                data_date=data_date,
                **page_fields(result),
            )

        return await self._policy.get_or_load(
            "newsflash", key, loader, MarketPageResponse[NewsFlashOut].model_validate
        )

    async def monitor(self, *, on_date: date | None, kind: str | None) -> MonitorResponse:
        """取某日监管名单（``date`` 缺省用库中最新）。"""
        key = query_key("monitor", {"date": _iso(on_date), "kind": kind})

        async def loader() -> MonitorResponse:
            target = on_date or await self._read.latest_date(MonitorStock, "trade_date")
            rows = await self._repos.monitor_stocks.get_by_date(target, kind) if target else []
            stale, data_date = await market_freshness(self._read, MonitorStock)
            return MonitorResponse(
                trade_date=target,
                kind=kind,
                items=[MonitorStockOut.model_validate(row) for row in rows],
                stale=stale,
                data_date=data_date,
            )

        return await self._policy.get_or_load(
            "monitor", key, loader, MonitorResponse.model_validate
        )
