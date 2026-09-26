"""只读行情工具（从 :mod:`app.agent.tools.read` 拆出）：股票/日线/分时/池/天梯/情绪/主题/快讯/监管。

全部经 :class:`~app.services.market_service.MarketService` 访问 DB，不重复实现
查询逻辑，也绝不触达上游数据源。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.agent.tools.base import AgentTool, ToolContext, register_tool, tool_schema
from app.services.market_service import MarketService

__all__ = [
    "QueryDailyBarsTool",
    "QueryLadderTool",
    "QueryLimitUpPoolTool",
    "QueryMinuteBarsTool",
    "QueryMonitorTool",
    "QueryNewsflashTool",
    "QuerySentimentTool",
    "QueryStocksTool",
    "QueryThemeTool",
]


def _dump(value: Any) -> Any:
    """把 Pydantic 响应模型转为 JSON 友好字典。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


# ------------------------------------------------------------------ 行情


class QueryStocksArgs(BaseModel):
    """``query_stocks`` 入参。"""

    keyword: str | None = Field(default=None, description="代码或简称关键词")
    page: int = Field(default=1, ge=1, description="页码，从 1 起")
    page_size: int = Field(default=20, ge=1, le=200, description="页大小（上限 200）")


@register_tool
class QueryStocksTool(AgentTool):
    """按关键词分页检索股票基础信息。"""

    name = "query_stocks"
    description = "按代码或简称关键词分页检索股票基础信息（代码/名称/市场/板块/是否 ST）。"
    args_model = QueryStocksArgs
    parameters = tool_schema(QueryStocksArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`MarketService.stocks`。"""
        service = MarketService(ctx.repos)
        result = await service.stocks(
            keyword=kwargs["keyword"], page=kwargs["page"], page_size=kwargs["page_size"]
        )
        return _dump(result)


class QueryDailyBarsArgs(BaseModel):
    """``query_daily_bars`` 入参。"""

    code: str = Field(description="证券代码，如 600001")
    start: date = Field(description="起始交易日（含）")
    end: date = Field(description="结束交易日（含）")


@register_tool
class QueryDailyBarsTool(AgentTool):
    """取某只股票区间日线。"""

    name = "query_daily_bars"
    description = "取某只股票在 [start, end] 区间的日线行情（开高低收/前收/量/额）。"
    args_model = QueryDailyBarsArgs
    parameters = tool_schema(QueryDailyBarsArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`MarketService.daily_bars`。"""
        service = MarketService(ctx.repos)
        result = await service.daily_bars(
            code=kwargs["code"], start=kwargs["start"], end=kwargs["end"]
        )
        return _dump(result)


class QueryMinuteBarsArgs(BaseModel):
    """``query_minute_bars`` 入参。"""

    code: str = Field(description="证券代码，如 600001")
    trade_date: date = Field(description="交易日")


@register_tool
class QueryMinuteBarsTool(AgentTool):
    """取某只股票某日全部分时。"""

    name = "query_minute_bars"
    description = "取某只股票某交易日的全部分时序列（分钟序号/时间标签/价格/量/额）。"
    args_model = QueryMinuteBarsArgs
    parameters = tool_schema(QueryMinuteBarsArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`MarketService.minute_bars`。"""
        service = MarketService(ctx.repos)
        result = await service.minute_bars(code=kwargs["code"], on_date=kwargs["trade_date"])
        return _dump(result)


class QueryLimitUpPoolArgs(BaseModel):
    """``query_limit_up_pool`` 入参。"""

    pool_type: str = Field(
        default="limit_up",
        description="池型：limit_up/limit_down/broken/prev_limit_up/strong/new_stock/sub_new",
    )
    trade_date: date | None = Field(default=None, description="交易日；缺省用库中最新")
    min_continue_days: int = Field(default=1, ge=1, le=20, description="连板天数下界")


@register_tool
class QueryLimitUpPoolTool(AgentTool):
    """取某日某池型成分。"""

    name = "query_limit_up_pool"
    description = "取某交易日指定池型的成分股（连板天数/封板时间/封单额/换手率/成交额）。"
    args_model = QueryLimitUpPoolArgs
    parameters = tool_schema(QueryLimitUpPoolArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`MarketService.pool`。"""
        service = MarketService(ctx.repos)
        result = await service.pool(
            pool_type=kwargs["pool_type"],
            on_date=kwargs["trade_date"],
            min_continue_days=kwargs["min_continue_days"],
        )
        return _dump(result)


class QueryLadderArgs(BaseModel):
    """``query_ladder`` 入参。"""

    start: date = Field(description="起始交易日（含）")
    end: date = Field(description="结束交易日（含）")
    min_continue_days: int = Field(default=2, ge=1, le=20, description="连板天数下界")
    page: int = Field(default=1, ge=1, description="页码，从 1 起")
    page_size: int = Field(default=50, ge=1, le=200, description="页大小（上限 200）")


@register_tool
class QueryLadderTool(AgentTool):
    """取连板天梯。"""

    name = "query_ladder"
    description = "分页取区间内连板天数达标的连板天梯记录（按交易日、连板天数降序）。"
    args_model = QueryLadderArgs
    parameters = tool_schema(QueryLadderArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`MarketService.ladder`。"""
        service = MarketService(ctx.repos)
        result = await service.ladder(
            start=kwargs["start"],
            end=kwargs["end"],
            min_continue_days=kwargs["min_continue_days"],
            page=kwargs["page"],
            page_size=kwargs["page_size"],
        )
        return _dump(result)


class QuerySentimentArgs(BaseModel):
    """``query_sentiment`` 入参。"""

    trade_date: date | None = Field(default=None, description="交易日；缺省用库中最新")
    days: int | None = Field(
        default=None, ge=1, le=200, description="给定则返回最近 days 个交易日的情绪序列"
    )


@register_tool
class QuerySentimentTool(AgentTool):
    """取市场情绪（单日或最近若干日）。"""

    name = "query_sentiment"
    description = "取市场情绪指标（情绪温度/周期阶段/涨跌停家数/炸板率/溢价率/最高连板）。"
    args_model = QuerySentimentArgs
    parameters = tool_schema(QuerySentimentArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """``days`` 给定走历史序列，否则取单日。"""
        service = MarketService(ctx.repos)
        if kwargs["days"] is not None:
            return _dump(await service.sentiment_history(kwargs["days"]))
        return _dump(await service.sentiment(kwargs["trade_date"]))


class QueryThemeArgs(BaseModel):
    """``query_theme`` 入参。"""

    trade_date: date | None = Field(default=None, description="交易日；缺省用库中最新")
    theme_name: str | None = Field(
        default=None, description="给定则返回该主题的成分股，否则返回主题强度榜"
    )


@register_tool
class QueryThemeTool(AgentTool):
    """取主题强度榜或某主题成分股。"""

    name = "query_theme"
    description = "取某交易日的主题强度榜；给定 theme_name 时返回该主题的成分股明细。"
    args_model = QueryThemeArgs
    parameters = tool_schema(QueryThemeArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """``theme_name`` 给定走成分股，否则走强度榜。"""
        service = MarketService(ctx.repos)
        name = kwargs["theme_name"]
        if name:
            target = kwargs["trade_date"]
            if target is None:
                available = await ctx.repos.themes.available_dates(1)
                target = available[0] if available else None
            if target is None:
                return {"trade_date": None, "theme_name": name, "items": []}
            return _dump(await service.theme_stocks(on_date=target, theme_name=name))
        return _dump(await service.themes(kwargs["trade_date"]))


class QueryNewsflashArgs(BaseModel):
    """``query_newsflash`` 入参。"""

    keyword: str | None = Field(default=None, description="关键词（命中标题或摘要）")
    page: int = Field(default=1, ge=1, description="页码，从 1 起")
    page_size: int = Field(default=20, ge=1, le=200, description="页大小（上限 200）")


@register_tool
class QueryNewsflashTool(AgentTool):
    """分页检索快讯。"""

    name = "query_newsflash"
    description = "分页检索 7×24 快讯（按发布时间倒序，可按关键词过滤）。"
    args_model = QueryNewsflashArgs
    parameters = tool_schema(QueryNewsflashArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`MarketService.newsflash`。"""
        service = MarketService(ctx.repos)
        result = await service.newsflash(
            keyword=kwargs["keyword"],
            page=kwargs["page"],
            page_size=kwargs["page_size"],
        )
        return _dump(result)


class QueryMonitorArgs(BaseModel):
    """``query_monitor`` 入参。"""

    trade_date: date | None = Field(default=None, description="交易日；缺省用库中最新")
    kind: str | None = Field(
        default=None,
        description=(
            "类型：restricted（交易所重点监控）/ severe（严重异常波动）/ "
            "unusual（普通异常波动）；缺省不过滤"
        ),
    )


@register_tool
class QueryMonitorTool(AgentTool):
    """取监管名单。"""

    name = "query_monitor"
    description = "取某交易日监管名单（重点监控 / 异常波动）。"
    args_model = QueryMonitorArgs
    parameters = tool_schema(QueryMonitorArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`MarketService.monitor`。"""
        service = MarketService(ctx.repos)
        result = await service.monitor(on_date=kwargs["trade_date"], kind=kwargs["kind"])
        return _dump(result)
