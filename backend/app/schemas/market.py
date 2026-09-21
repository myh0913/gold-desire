"""行情域响应契约：股票、日线/分时、涨停池、天梯、情绪、主题、快讯、监管名单。

字段名与单位沿用 ``app/models/market.py`` 的领域标准名（价格=元，量=股/手，
比率=小数口径）。行情类响应继承 :class:`~app.schemas.common.MarketMeta`，携带
``stale`` / ``data_date`` 新鲜度标记。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import MarketMeta

__all__ = [
    "DailyBarOut",
    "DailyBarsResponse",
    "LadderRowOut",
    "LimitUpPoolOut",
    "MinuteBarOut",
    "MinuteBarsResponse",
    "MonitorResponse",
    "MonitorStockOut",
    "NewsFlashOut",
    "PoolResponse",
    "PoolsResponse",
    "SentimentHistoryResponse",
    "SentimentOut",
    "SentimentResponse",
    "StockOut",
    "ThemeOut",
    "ThemeStockOut",
    "ThemeStocksResponse",
    "ThemesResponse",
]

_ORM = ConfigDict(from_attributes=True)


class StockOut(BaseModel):
    """股票基础信息。"""

    model_config = _ORM

    code: str
    name: str
    market: str
    board: str
    is_st: bool
    list_date: date | None = None


class DailyBarOut(BaseModel):
    """日线行情（价格元，``volume_shares`` 股，``amount_yuan`` 元）。"""

    model_config = _ORM

    code: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    pre_close: float | None = None
    volume_shares: int
    amount_yuan: float


class DailyBarsResponse(MarketMeta):
    """某只股票区间日线序列。"""

    code: str
    start: date
    end: date
    items: list[DailyBarOut] = Field(default_factory=list)


class MinuteBarOut(BaseModel):
    """分时行情（``price`` 元，``volume_lots`` 手；``amount_yuan`` eltdx 源为 null）。"""

    model_config = _ORM

    code: str
    trade_date: date
    minute_index: int
    time_label: str
    price: float
    volume_lots: int
    amount_yuan: float | None = None


class MinuteBarsResponse(MarketMeta):
    """某只股票某日分时序列。"""

    code: str
    trade_date: date
    items: list[MinuteBarOut] = Field(default_factory=list)


class LimitUpPoolOut(BaseModel):
    """涨停池成分（``turnover_rate`` / ``change_pct`` / ``seal_ratio`` 小数口径）。"""

    model_config = _ORM

    trade_date: date
    code: str
    name: str
    continue_days: int
    limit_up_time: str | None = None
    seal_amount_yuan: float | None = None
    open_times: int | None = None
    turnover_rate: float | None = None
    amount_yuan: float | None = None
    market_cap_yuan: float | None = None
    price: float | None = None
    change_pct: float | None = None
    volume_bias_ratio: float | None = None
    free_cap_yuan: float | None = None
    seal_ratio: float | None = None
    reason: str | None = None
    plates: list[dict[str, Any]] | None = None
    timeline: list[dict[str, Any]] | None = None
    pool_type: str


class PoolsResponse(MarketMeta):
    """某交易日全部池型（``pool_type -> 成分``）。"""

    trade_date: date | None = None
    pools: dict[str, list[LimitUpPoolOut]] = Field(default_factory=dict)


class PoolResponse(MarketMeta):
    """某交易日单个池型成分。"""

    trade_date: date | None = None
    pool_type: str
    min_continue_days: int
    items: list[LimitUpPoolOut] = Field(default_factory=list)


class LadderRowOut(BaseModel):
    """连板天梯单行。"""

    model_config = _ORM

    trade_date: date
    code: str
    name: str
    continue_days: int
    first_seal_time: str | None = None


class SentimentOut(BaseModel):
    """市场情绪指标（``temperature`` 分，比率字段小数口径）。"""

    model_config = _ORM

    trade_date: date
    temperature: float
    stage: str | None = None
    limit_up_count: int
    limit_down_count: int
    broken_board_count: int
    broken_rate: float
    up_count: int
    down_count: int
    max_continue_days: int | None = None
    premium_rate: float


class SentimentResponse(MarketMeta):
    """某交易日情绪指标。"""

    trade_date: date | None = None
    item: SentimentOut | None = None


class SentimentHistoryResponse(MarketMeta):
    """最近 ``days`` 个交易日情绪（按交易日升序）。"""

    days: int
    items: list[SentimentOut] = Field(default_factory=list)


class ThemeOut(BaseModel):
    """主题强度榜单项。"""

    model_config = _ORM

    trade_date: date
    rank: int
    name: str
    core_avg_pct: float | None = None
    description: str | None = None
    core_count: int | None = None


class ThemesResponse(MarketMeta):
    """某交易日主题强度榜。"""

    trade_date: date | None = None
    items: list[ThemeOut] = Field(default_factory=list)


class ThemeStockOut(BaseModel):
    """主题成分股。"""

    model_config = _ORM

    trade_date: date
    theme_name: str
    code: str
    name: str
    price: float
    pct: float
    turnover_rate: float
    continue_days: int | None = None
    selected_at: datetime | None = None


class ThemeStocksResponse(MarketMeta):
    """某日某主题的成分股。"""

    trade_date: date
    theme_name: str
    items: list[ThemeStockOut] = Field(default_factory=list)


class NewsFlashOut(BaseModel):
    """快讯。"""

    model_config = _ORM

    ts: datetime
    level: str | None = None
    title: str
    summary: str | None = None
    symbols: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class MonitorStockOut(BaseModel):
    """监管名单记录。"""

    model_config = _ORM

    trade_date: date
    kind: str
    code: str
    name: str
    reason: str | None = None


class MonitorResponse(MarketMeta):
    """某日监管名单。"""

    trade_date: date | None = None
    kind: str | None = None
    items: list[MonitorStockOut] = Field(default_factory=list)
