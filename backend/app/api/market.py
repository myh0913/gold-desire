"""行情读路由（全部需认证；有页面 key 的按页面校验）。

只读：路由仅调用 :class:`~app.services.market_service.MarketService`，
**不直接触达仓储/ORM，也不触达上游**。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user, require_page
from app.core.pages import PageKey
from app.models.auth import User
from app.repositories import Repositories, get_repositories
from app.schemas.common import DatesResponse, MarketPageResponse, PageResponse
from app.schemas.market import (
    DailyBarsResponse,
    LadderRowOut,
    MinuteBarsResponse,
    MonitorResponse,
    NewsFlashOut,
    PoolResponse,
    PoolsResponse,
    SentimentHistoryResponse,
    SentimentResponse,
    StockOut,
    ThemesResponse,
    ThemeStocksResponse,
)
from app.services.market_service import MarketService

router = APIRouter(tags=["market"])


def get_market_service(repos: Repositories = Depends(get_repositories)) -> MarketService:
    """请求级行情服务。"""
    return MarketService(repos)


@router.get("/stocks", response_model=PageResponse[StockOut])
async def list_stocks(
    keyword: str | None = Query(default=None, max_length=64, description="代码/简称关键词"),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, description="超上限自动收敛"),
    _: User = Depends(get_current_user),
    service: MarketService = Depends(get_market_service),
) -> PageResponse[StockOut]:
    """分页检索股票（强制有界）。"""
    return await service.stocks(keyword=keyword, page=page, page_size=page_size)


@router.get("/stocks/{code}", response_model=StockOut)
async def get_stock(
    code: str,
    _: User = Depends(get_current_user),
    service: MarketService = Depends(get_market_service),
) -> StockOut:
    """取单只股票基础信息。"""
    return await service.stock(code)


@router.get("/bars/daily", response_model=DailyBarsResponse)
async def get_daily_bars(
    code: str = Query(min_length=1, max_length=16),
    start: date = Query(description="起始交易日（含）"),
    end: date = Query(description="结束交易日（含）"),
    _: User = Depends(get_current_user),
    service: MarketService = Depends(get_market_service),
) -> DailyBarsResponse:
    """取某只股票区间日线。"""
    return await service.daily_bars(code=code, start=start, end=end)


@router.get("/bars/minute", response_model=MinuteBarsResponse)
async def get_minute_bars(
    code: str = Query(min_length=1, max_length=16),
    date_: date = Query(alias="date", description="交易日"),
    _: User = Depends(get_current_user),
    service: MarketService = Depends(get_market_service),
) -> MinuteBarsResponse:
    """取某只股票某日全部分时。"""
    return await service.minute_bars(code=code, on_date=date_)


@router.get("/pools", response_model=PoolsResponse)
async def list_pools(
    date_: date | None = Query(default=None, alias="date", description="交易日；缺省取最新"),
    _: User = Depends(require_page(PageKey.POOLS)),
    service: MarketService = Depends(get_market_service),
) -> PoolsResponse:
    """取某日全部池型。"""
    return await service.pools(date_)


@router.get("/pools/{pool_type}", response_model=PoolResponse)
async def get_pool(
    pool_type: str,
    date_: date | None = Query(default=None, alias="date", description="交易日；缺省取最新"),
    min_continue_days: int = Query(default=1, ge=1, description="连板天数下界"),
    _: User = Depends(require_page(PageKey.POOLS)),
    service: MarketService = Depends(get_market_service),
) -> PoolResponse:
    """取某日某池型成分。"""
    return await service.pool(
        pool_type=pool_type, on_date=date_, min_continue_days=min_continue_days
    )


@router.get("/ladder", response_model=MarketPageResponse[LadderRowOut])
async def get_ladder(
    start: date = Query(description="起始交易日（含）"),
    end: date = Query(description="结束交易日（含）"),
    min_continue_days: int = Query(default=2, ge=1, description="连板天数下界"),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    _: User = Depends(require_page(PageKey.LADDER)),
    service: MarketService = Depends(get_market_service),
) -> MarketPageResponse[LadderRowOut]:
    """分页取连板天梯（强制有界）。"""
    return await service.ladder(
        start=start,
        end=end,
        min_continue_days=min_continue_days,
        page=page,
        page_size=page_size,
    )


@router.get("/ladder/dates", response_model=DatesResponse)
async def get_ladder_dates(
    limit: int | None = Query(default=None, ge=1),
    _: User = Depends(require_page(PageKey.LADDER)),
    service: MarketService = Depends(get_market_service),
) -> DatesResponse:
    """可取的天梯交易日列表（去重倒序）。"""
    return await service.ladder_dates(limit)


@router.get("/sentiment", response_model=SentimentResponse)
async def get_sentiment(
    date_: date | None = Query(default=None, alias="date"),
    _: User = Depends(require_page(PageKey.OVERVIEW)),
    service: MarketService = Depends(get_market_service),
) -> SentimentResponse:
    """取某交易日情绪指标（缺省取库中最新）。"""
    return await service.sentiment(date_)


@router.get("/sentiment/history", response_model=SentimentHistoryResponse)
async def get_sentiment_history(
    days: int | None = Query(default=None, ge=1),
    _: User = Depends(require_page(PageKey.OVERVIEW)),
    service: MarketService = Depends(get_market_service),
) -> SentimentHistoryResponse:
    """取最近 ``days`` 个交易日情绪（升序）。"""
    return await service.sentiment_history(days)


@router.get("/themes", response_model=ThemesResponse)
async def get_themes(
    date_: date | None = Query(default=None, alias="date"),
    _: User = Depends(require_page(PageKey.THEMES)),
    service: MarketService = Depends(get_market_service),
) -> ThemesResponse:
    """取某交易日主题强度榜（缺省取库中最新）。"""
    return await service.themes(date_)


@router.get("/themes/dates", response_model=DatesResponse)
async def get_theme_dates(
    limit: int | None = Query(default=None, ge=1),
    _: User = Depends(require_page(PageKey.THEMES)),
    service: MarketService = Depends(get_market_service),
) -> DatesResponse:
    """可取的主题交易日列表（去重倒序）。"""
    return await service.theme_dates(limit)


@router.get("/themes/{on_date}/{theme_name}/stocks", response_model=ThemeStocksResponse)
async def get_theme_stocks(
    on_date: date,
    theme_name: str,
    _: User = Depends(require_page(PageKey.THEMES)),
    service: MarketService = Depends(get_market_service),
) -> ThemeStocksResponse:
    """取某日某主题的成分股。"""
    return await service.theme_stocks(on_date=on_date, theme_name=theme_name)


@router.get("/newsflash", response_model=MarketPageResponse[NewsFlashOut])
async def list_newsflash(
    level: str | None = Query(default=None, max_length=16),
    keyword: str | None = Query(default=None, max_length=64),
    limit: int | None = Query(default=None, ge=1, description="``limit`` 等价于 ``page_size``"),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    _: User = Depends(require_page(PageKey.NEWSFLASH)),
    service: MarketService = Depends(get_market_service),
) -> MarketPageResponse[NewsFlashOut]:
    """分页检索快讯（``limit`` 与 ``page_size`` 二者取一，均受上限约束）。"""
    effective_size = limit if limit is not None else page_size
    return await service.newsflash(
        level=level, keyword=keyword, page=page, page_size=effective_size
    )


@router.get("/monitor", response_model=MonitorResponse)
async def get_monitor(
    date_: date | None = Query(default=None, alias="date"),
    kind: str | None = Query(default=None, max_length=32),
    _: User = Depends(require_page(PageKey.MONITOR)),
    service: MarketService = Depends(get_market_service),
) -> MonitorResponse:
    """取某日监管名单（缺省取库中最新）。"""
    return await service.monitor(on_date=date_, kind=kind)


__all__ = ["get_market_service", "router"]
