"""复盘域响应契约：某交易日的市场情绪 / 池型统计 / 天梯头部 / 建议回溯。

复盘为**派生只读视图**（不落库）：由情绪行、涨停池、建议报告与日线实时聚合，
其中「建议回溯」按策略卖出规则（无止盈 + 3% 止损 + 持 1 个可卖日）以日线口径
逐条评估结果。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from app.schemas.market import SentimentOut

__all__ = [
    "ReviewAdviceOutcomeOut",
    "ReviewAdviceStatsOut",
    "ReviewPoolTopOut",
    "ReviewResponse",
]


class ReviewPoolTopOut(BaseModel):
    """涨停池头部个股（按连板天数倒序）。"""

    code: str
    name: str
    continue_days: int
    limit_up_time: str | None = None
    seal_amount_yuan: float | None = None
    turnover_rate: float | None = None


class ReviewAdviceOutcomeOut(BaseModel):
    """单条建议的回溯结果。

    ``status`` 口径：

    - ``pending``：可卖日日线尚未入库（未到或未采集），暂无法评估；
    - ``stopped``：可卖日最低价触及止损价 → 按 ``stop_loss_price`` 成交；
    - ``closed``：持到可卖日收盘了结 → 按 ``sell_price``（收盘价）成交。
    """

    code: str
    name: str | None = None
    path_id: str
    path_label: str | None = None
    buy_day: date | None = None
    buy_price: float | None = None
    position: float | None = None
    stop_loss_price: float | None = None
    sell_timing: str | None = None
    bonus_score: int | None = None
    status: str
    return_pct: float | None = None
    sell_date: date | None = None
    sell_price: float | None = None
    ran_at: str | None = None


class ReviewAdviceStatsOut(BaseModel):
    """建议回溯汇总（胜率 / 平均收益按已了结条目计）。"""

    total: int = 0
    settled: int = 0
    pending: int = 0
    win_count: int = 0
    win_rate: float | None = None
    avg_return_pct: float | None = None


class ReviewResponse(BaseModel):
    """某交易日复盘聚合视图。"""

    trade_date: date | None = None
    sentiment: SentimentOut | None = None
    prev_trade_date: date | None = None
    prev_temperature: float | None = None
    temperature_delta: float | None = None
    pool_counts: dict[str, int] = {}
    top_ladder: list[ReviewPoolTopOut] = []
    advices: list[ReviewAdviceOutcomeOut] = []
    advice_stats: ReviewAdviceStatsOut = ReviewAdviceStatsOut()
    meta: dict[str, Any] = {}
