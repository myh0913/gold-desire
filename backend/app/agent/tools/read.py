"""只读工具：所有已认证角色可用，全部经既有服务层访问 DB + 缓存。

每个工具复用 REST API 的服务方法（:class:`~app.services.market_service.MarketService`、
:class:`~app.services.report_service.ReportService`、
:class:`~app.services.config_service.ConfigService`、
:class:`~app.services.datasource_service.DatasourceService`、
:class:`~app.services.ingest_service.IngestService`），不重复实现查询逻辑，
也绝不触达上游数据源。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.agent.tools.base import AgentTool, ToolContext, register_tool, tool_schema
from app.services.config_service import ConfigService
from app.services.datasource_service import DatasourceService
from app.services.ingest_service import IngestService
from app.services.market_service import MarketService
from app.services.report_service import ReportService

__all__ = [
    "GetFactorEffectivenessTool",
    "GetFactorTool",
    "GetIngestHealthTool",
    "GetStrategyTool",
    "ListDatasourcesTool",
    "ListFactorsTool",
    "ListStrategiesTool",
    "QueryAdviceTool",
    "QueryBacktestTool",
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

    level: str | None = Field(default=None, description="重要级别，如 high/low")
    keyword: str | None = Field(default=None, description="关键词（命中标题或摘要）")
    page: int = Field(default=1, ge=1, description="页码，从 1 起")
    page_size: int = Field(default=20, ge=1, le=200, description="页大小（上限 200）")


@register_tool
class QueryNewsflashTool(AgentTool):
    """分页检索快讯。"""

    name = "query_newsflash"
    description = "分页检索 7×24 快讯（按发布时间倒序，可按重要级别与关键词过滤）。"
    args_model = QueryNewsflashArgs
    parameters = tool_schema(QueryNewsflashArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`MarketService.newsflash`。"""
        service = MarketService(ctx.repos)
        result = await service.newsflash(
            level=kwargs["level"],
            keyword=kwargs["keyword"],
            page=kwargs["page"],
            page_size=kwargs["page_size"],
        )
        return _dump(result)


class QueryMonitorArgs(BaseModel):
    """``query_monitor`` 入参。"""

    trade_date: date | None = Field(default=None, description="交易日；缺省用库中最新")
    kind: str | None = Field(
        default=None, description="类型：key_monitor（重点监控）/ severe_unusual（严重异常波动）"
    )


@register_tool
class QueryMonitorTool(AgentTool):
    """取监管名单。"""

    name = "query_monitor"
    description = "取某交易日监管名单（重点监控 / 严重异常波动）。"
    args_model = QueryMonitorArgs
    parameters = tool_schema(QueryMonitorArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`MarketService.monitor`。"""
        service = MarketService(ctx.repos)
        result = await service.monitor(on_date=kwargs["trade_date"], kind=kwargs["kind"])
        return _dump(result)


# ------------------------------------------------------------------ 报告


class QueryAdviceArgs(BaseModel):
    """``query_advice`` 入参。"""

    trade_date: date | None = Field(default=None, description="交易日；缺省用最近有报告的交易日")
    kind: str | None = Field(default=None, description="报告类型，如 advice/error")
    strategy_id: str | None = Field(default=None, description="策略标识，如 dragon")


@register_tool
class QueryAdviceTool(AgentTool):
    """取策略建议报告。"""

    name = "query_advice"
    description = "取某交易日的策略建议报告（含命中门槛/加分项/建议仓位/止损价/卖出时点）。"
    args_model = QueryAdviceArgs
    parameters = tool_schema(QueryAdviceArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ReportService.advice`。"""
        service = ReportService(ctx.repos)
        result = await service.advice(
            on_date=kwargs["trade_date"],
            kind=kwargs["kind"],
            strategy_id=kwargs["strategy_id"],
        )
        return _dump(result)


class QueryBacktestArgs(BaseModel):
    """``query_backtest`` 入参。"""

    run_id: str | None = Field(default=None, description="回测任务 ID；缺省返回最近任务列表")
    limit: int = Field(default=10, ge=1, le=200, description="列表条数上限")


@register_tool
class QueryBacktestTool(AgentTool):
    """取回测任务或最近回测列表。"""

    name = "query_backtest"
    description = "取单个回测任务的 A/B/C 三段报告，或缺省返回最近回测任务列表。"
    args_model = QueryBacktestArgs
    parameters = tool_schema(QueryBacktestArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """``run_id`` 给定取单任务，否则取列表。"""
        service = ReportService(ctx.repos)
        run_id = kwargs["run_id"]
        if run_id:
            return _dump(await service.backtest_run(run_id))
        return _dump(await service.backtest_runs(kwargs["limit"]))


# ------------------------------------------------------------------ 配置


class ListStrategiesArgs(BaseModel):
    """``list_strategies`` 入参（无）。"""


@register_tool
class ListStrategiesTool(AgentTool):
    """列出全部策略定义与生效参数。"""

    name = "list_strategies"
    description = "列出全部策略（定义、参数 schema、生效参数与版本历史）。"
    args_model = ListStrategiesArgs
    parameters = tool_schema(ListStrategiesArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ConfigService.strategies`。"""
        return _dump(await ConfigService(ctx.repos).strategies())


class GetStrategyArgs(BaseModel):
    """``get_strategy`` 入参。"""

    strategy_id: str = Field(description="策略标识，如 dragon")


@register_tool
class GetStrategyTool(AgentTool):
    """取单个策略详情。"""

    name = "get_strategy"
    description = "取单个策略的完整定义（含门控矩阵、参数 schema、生效参数与版本历史）。"
    args_model = GetStrategyArgs
    parameters = tool_schema(GetStrategyArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ConfigService.strategy`。"""
        return _dump(await ConfigService(ctx.repos).strategy(kwargs["strategy_id"]))


class ListFactorsArgs(BaseModel):
    """``list_factors`` 入参（无）。"""


@register_tool
class ListFactorsTool(AgentTool):
    """列出全部因子定义与生效参数。"""

    name = "list_factors"
    description = "列出全部因子（定义、参数 schema、档位与生效参数）。"
    args_model = ListFactorsArgs
    parameters = tool_schema(ListFactorsArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ConfigService.factors`。"""
        return _dump(await ConfigService(ctx.repos).factors())


class GetFactorArgs(BaseModel):
    """``get_factor`` 入参。"""

    factor_id: str = Field(description="因子标识，如 first_yin_amplitude")


@register_tool
class GetFactorTool(AgentTool):
    """取单个因子详情。"""

    name = "get_factor"
    description = "取单个因子的完整定义（参数 schema、档位定义与生效参数）。"
    args_model = GetFactorArgs
    parameters = tool_schema(GetFactorArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ConfigService.factor`。"""
        return _dump(await ConfigService(ctx.repos).factor(kwargs["factor_id"]))


class GetFactorEffectivenessArgs(BaseModel):
    """``get_factor_effectiveness`` 入参。"""

    factor_id: str = Field(description="因子标识，如 first_yin_amplitude")
    start: date | None = Field(default=None, description="统计起始日；缺省回溯 180 天")
    end: date | None = Field(default=None, description="统计结束日；缺省今天")
    min_boards: int = Field(default=2, ge=1, le=10, description="样本最小连板数")


@register_tool
class GetFactorEffectivenessTool(AgentTool):
    """取因子有效性统计。"""

    name = "get_factor_effectiveness"
    description = "取因子有效性统计（按档位输出样本数/期望/胜率，且**必须含 A/B/C 分段**）。"
    args_model = GetFactorEffectivenessArgs
    parameters = tool_schema(GetFactorEffectivenessArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ConfigService.factor_effectiveness`。"""
        service = ConfigService(ctx.repos)
        result = await service.factor_effectiveness(
            kwargs["factor_id"],
            start=kwargs["start"],
            end=kwargs["end"],
            min_boards=kwargs["min_boards"],
        )
        return _dump(result)


# ------------------------------------------------------------------ 运维


class GetIngestHealthArgs(BaseModel):
    """``get_ingest_health`` 入参（无）。"""


@register_tool
class GetIngestHealthTool(AgentTool):
    """取采集健康度。"""

    name = "get_ingest_health"
    description = "取采集健康度汇总（各能力最近成功/失败时间与连续失败次数）。"
    args_model = GetIngestHealthArgs
    parameters = tool_schema(GetIngestHealthArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`IngestService.health`。"""
        return _dump(await IngestService(ctx.repos).health())


class ListDatasourcesArgs(BaseModel):
    """``list_datasources`` 入参（无）。"""


@register_tool
class ListDatasourcesTool(AgentTool):
    """列出数据源注册表与健康度。"""

    name = "list_datasources"
    description = "列出数据源注册表（能力清单/限频/启停/优先级/最新健康度）与能力取数顺序。"
    args_model = ListDatasourcesArgs
    parameters = tool_schema(ListDatasourcesArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`DatasourceService.datasources`。"""
        return _dump(await DatasourceService(ctx.repos).datasources())
