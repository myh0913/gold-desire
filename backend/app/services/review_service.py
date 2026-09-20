"""复盘服务：聚合情绪 / 池型 / 天梯 / 建议回溯为只读复盘视图。

建议回溯的评估口径（与 readme §6 / §14 对齐的**日线近似**）：

- 可卖日 = 买入日之后库内的**第一个交易日**（取该股日线序列）；
- 若可卖日 ``low <= stop_loss_price`` → 止损成交（``return = stop/buy - 1``）；
- 否则持到可卖日收盘（``return = close/buy - 1``）；
- 可卖日未入库 → ``pending``。

> 与分钟级止损撮合的差异：日线口径无法捕捉盘中先止跌后新低等路径细节，
> 结果为保守近似；回测引擎（分钟级）以 :mod:`app.engine.sell_rules` 为准。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.repositories import Repositories
from app.schemas.common import DatesResponse
from app.schemas.market import SentimentOut
from app.schemas.review import (
    ReviewAdviceOutcomeOut,
    ReviewAdviceStatsOut,
    ReviewPoolTopOut,
    ReviewResponse,
)

__all__ = ["ReviewService"]

logger = logging.getLogger(__name__)

#: 建议回溯时向后找可卖日的自然日跨度。
_SELLABLE_LOOKAHEAD_DAYS = 14

#: 复盘页天梯头部条数。
_TOP_LADDER_LIMIT = 10


class ReviewService:
    """复盘聚合服务（只读派生，无落库）。"""

    def __init__(self, repos: Repositories) -> None:
        self._repos = repos

    # ------------------------------------------------------------------ 日期

    async def review_dates(self, limit: int | None) -> DatesResponse:
        """可复盘交易日（取自情绪行，去重倒序）。"""
        effective = max(1, min(limit or 30, 200))
        rows = await self._repos.market_sentiment.get_history(effective)
        dates = sorted({row.trade_date for row in rows}, reverse=True)
        return DatesResponse(dates=dates, limit=effective)

    # ------------------------------------------------------------------ 聚合

    async def review(self, on_date: date | None) -> ReviewResponse:
        """聚合某交易日复盘；``on_date`` 缺省取最新有情绪数据的交易日。"""
        target = on_date
        if target is None:
            latest = await self._repos.market_sentiment.latest()
            target = latest.trade_date if latest is not None else None
        if target is None:
            return ReviewResponse(trade_date=None)

        sentiment_row = await self._repos.market_sentiment.get(target)
        sentiment = SentimentOut.model_validate(sentiment_row) if sentiment_row else None
        prev_date, prev_temperature = await self._prev_sentiment(target)

        pool_rows = await self._repos.limit_up_pool.get_by_date(target)
        pool_counts: dict[str, int] = {}
        for row in pool_rows:
            pool_counts[row.pool_type] = pool_counts.get(row.pool_type, 0) + 1
        top_ladder = [
            ReviewPoolTopOut(
                code=row.code,
                name=row.name,
                continue_days=row.continue_days,
                limit_up_time=row.limit_up_time,
                seal_amount_yuan=(
                    float(row.seal_amount_yuan) if row.seal_amount_yuan is not None else None
                ),
                turnover_rate=(
                    float(row.turnover_rate) if row.turnover_rate is not None else None
                ),
            )
            for row in sorted(
                (item for item in pool_rows if item.pool_type == "limit_up"),
                key=lambda item: item.continue_days,
                reverse=True,
            )[:_TOP_LADDER_LIMIT]
        ]

        advices = await self._advice_outcomes(target)
        stats = self._stats(advices)

        return ReviewResponse(
            trade_date=target,
            sentiment=sentiment,
            prev_trade_date=prev_date,
            prev_temperature=prev_temperature,
            temperature_delta=(
                sentiment.temperature - prev_temperature
                if sentiment is not None and prev_temperature is not None
                else None
            ),
            pool_counts=pool_counts,
            top_ladder=top_ladder,
            advices=advices,
            advice_stats=stats,
        )

    # ------------------------------------------------------------------ 内部

    async def _prev_sentiment(self, target: date) -> tuple[date | None, float | None]:
        """取目标日前一个交易日的情绪温度（无则 ``None``）。"""
        rows = await self._repos.market_sentiment.get_history(90)
        ordered = sorted(rows, key=lambda row: row.trade_date)
        previous = None
        for row in ordered:
            if row.trade_date < target:
                previous = row
            elif row.trade_date == target:
                break
        if previous is None:
            return None, None
        return previous.trade_date, float(previous.temperature)

    async def _advice_outcomes(self, target: date) -> list[ReviewAdviceOutcomeOut]:
        """某日全部建议的去重回溯结果。

        去重键 = ``(code, path_id, buy_day)``：同一报告日可能包含同票同路但
        **买点日不同**的多条建议（不同 D 日的样本），不能互相覆盖；
        键相同时保留最近一次运行（``ran_at`` 倒序在先）。
        """
        reports = await self._repos.advice_reports.get_by_date(target, kind="advice")
        seen: set[tuple[str, str, date | None]] = set()
        outcomes: list[ReviewAdviceOutcomeOut] = []
        for report in reports:  # 已按 ran_at 倒序
            payload: dict[str, Any] = report.payload or {}
            code = str(payload.get("code") or "")
            path_id = str(payload.get("path_id") or "")
            buy_day = _parse_date(payload.get("buy_day"))
            if not code:
                continue
            key = (code, path_id, buy_day)
            if key in seen:
                continue
            seen.add(key)
            outcomes.append(await self._evaluate(payload, buy_day))
        return outcomes

    async def _evaluate(self, payload: dict[str, Any], buy_day: date | None) -> ReviewAdviceOutcomeOut:
        """按日线口径评估单条建议的结果。"""
        code = str(payload.get("code") or "")
        buy_price = _opt_float(payload.get("buy_price"))
        stop_price = _opt_float(payload.get("stop_loss_price"))

        base = ReviewAdviceOutcomeOut(
            code=code,
            name=payload.get("name"),
            path_id=str(payload.get("path_id") or "?"),
            path_label=payload.get("path_label"),
            buy_day=buy_day,
            buy_price=buy_price,
            position=_opt_float(payload.get("position")),
            stop_loss_price=stop_price,
            sell_timing=payload.get("sell_timing"),
            bonus_score=payload.get("bonus_score") if payload.get("bonus_score") is not None else None,
            status="pending",
            ran_at=payload.get("ran_at"),
        )
        if buy_day is None or not buy_price or buy_price <= 0:
            return base

        bars = await self._repos.daily_bars.get_range(
            code, buy_day + timedelta(days=1), buy_day + timedelta(days=_SELLABLE_LOOKAHEAD_DAYS)
        )
        if not bars:
            return base
        sell_bar = bars[0]
        if stop_price is not None and float(sell_bar.low) <= stop_price:
            return base.model_copy(
                update={
                    "status": "stopped",
                    "return_pct": stop_price / buy_price - 1,
                    "sell_date": sell_bar.trade_date,
                    "sell_price": stop_price,
                }
            )
        close = float(sell_bar.close)
        return base.model_copy(
            update={
                "status": "closed",
                "return_pct": close / buy_price - 1,
                "sell_date": sell_bar.trade_date,
                "sell_price": close,
            }
        )

    @staticmethod
    def _stats(advices: list[ReviewAdviceOutcomeOut]) -> ReviewAdviceStatsOut:
        """汇总：已了结（stopped/closed）计胜负，pending 单列。"""
        settled = [item for item in advices if item.status in ("stopped", "closed")]
        pending = sum(1 for item in advices if item.status == "pending")
        wins = sum(1 for item in settled if (item.return_pct or 0.0) > 0)
        returns = [item.return_pct for item in settled if item.return_pct is not None]
        return ReviewAdviceStatsOut(
            total=len(advices),
            settled=len(settled),
            pending=pending,
            win_count=wins,
            win_rate=(wins / len(settled)) if settled else None,
            avg_return_pct=(sum(returns) / len(returns)) if returns else None,
        )


def _parse_date(value: Any) -> date | None:
    """解析 ISO 日期串；非法返回 ``None``。"""
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _opt_float(value: Any) -> float | None:
    """宽容转 float；缺失/非法返回 ``None``。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
