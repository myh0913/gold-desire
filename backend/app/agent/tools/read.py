"""只读工具：所有已认证角色可用，全部经既有服务层访问 DB + 缓存。

每个工具复用 REST API 的服务方法（:class:`~app.services.market_service.MarketService`、
:class:`~app.services.report_service.ReportService`、
:class:`~app.services.config_service.ConfigService`、
:class:`~app.services.datasource_service.DatasourceService`、
:class:`~app.services.ingest_service.IngestService`），不重复实现查询逻辑，
也绝不触达上游数据源。

行情类查询拆至 :mod:`app.agent.tools.market_query`，在此 re-export 保持兼容。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.agent.tools.base import AgentTool, ToolContext, register_tool, tool_schema
from app.agent.tools.market_query import (
    QueryDailyBarsTool,
    QueryLadderTool,
    QueryLimitUpPoolTool,
    QueryMinuteBarsTool,
    QueryMonitorTool,
    QueryNewsflashTool,
    QuerySentimentTool,
    QueryStocksTool,
    QueryThemeTool,
    _dump,
)
from app.services.config_service import ConfigService
from app.services.datasource_service import DatasourceService
from app.services.ingest_service import IngestService
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
