"""变更工具：**仅 admin** 可用，全部复用 REST API 的服务函数并写审计。

- 有对应 REST 接口的操作（参数保存、版本回滚、策略启停、数据源启停、
  触发采集、触发回测）直接调用服务层同一方法，不重复实现业务逻辑；
- REST 未暴露的注册表维护（策略/因子定义的新增、修改、删除、版本激活）在
  :mod:`app.agent.tools.definitions`（此处 re-export 保持 import 路径不变）；
- 危险操作（删除、版本激活/回滚、触发采集/回测）标记 ``requires_confirmation=True``，
  首轮不执行，须经 HITL 确认端点二次确认。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.agent.tools.base import AgentTool, ToolContext, ToolError, register_tool, tool_schema
from app.agent.tools.definitions import (
    ActivateConfigVersionTool,
    CreateFactorTool,
    CreateStrategyTool,
    DeleteFactorTool,
    DeleteStrategyTool,
    RollbackConfigVersionTool,
    UpdateFactorConfigTool,
    UpdateFactorTool,
    UpdateStrategyConfigTool,
    UpdateStrategyTool,
    _dump,
)
from app.services.config_service import ConfigService
from app.services.datasource_service import DatasourceService
from app.services.ingest_service import IngestService
from app.services.report_service import ReportService

__all__ = [
    "ActivateConfigVersionTool",
    "CreateFactorTool",
    "CreateStrategyTool",
    "DeleteFactorTool",
    "DeleteStrategyTool",
    "DisableDatasourceTool",
    "DisableStrategyTool",
    "EnableDatasourceTool",
    "EnableStrategyTool",
    "RollbackConfigVersionTool",
    "RunBacktestTool",
    "SetDatasourcePriorityTool",
    "TriggerIngestTool",
    "UpdateFactorConfigTool",
    "UpdateFactorTool",
    "UpdateStrategyConfigTool",
    "UpdateStrategyTool",
]


# ------------------------------------------------------------------ 策略启停


class StrategyIdArgs(BaseModel):
    """仅需策略标识的入参。"""

    strategy_id: str = Field(min_length=1, max_length=64, description="策略标识")


@register_tool
class EnableStrategyTool(AgentTool):
    """启用策略（admin）。"""

    name = "enable_strategy"
    description = "启用指定策略。"
    mutating = True
    required_role = "admin"
    args_model = StrategyIdArgs
    parameters = tool_schema(StrategyIdArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ConfigService.set_strategy_enabled`。"""
        service = ConfigService(ctx.repos)
        result = await service.set_strategy_enabled(
            str(kwargs["strategy_id"]), True, actor=ctx.user.username
        )
        return _dump(result)


@register_tool
class DisableStrategyTool(AgentTool):
    """停用策略（admin）。"""

    name = "disable_strategy"
    description = "停用指定策略（下一轮调度即不再执行）。"
    mutating = True
    required_role = "admin"
    args_model = StrategyIdArgs
    parameters = tool_schema(StrategyIdArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ConfigService.set_strategy_enabled`。"""
        service = ConfigService(ctx.repos)
        result = await service.set_strategy_enabled(
            str(kwargs["strategy_id"]), False, actor=ctx.user.username
        )
        return _dump(result)


# ------------------------------------------------------------------ 数据源


class SourceIdArgs(BaseModel):
    """仅需数据源标识的入参。"""

    source_id: str = Field(min_length=1, max_length=32, description="数据源标识")


@register_tool
class EnableDatasourceTool(AgentTool):
    """启用数据源（admin）。"""

    name = "enable_datasource"
    description = "启用指定数据源。"
    mutating = True
    required_role = "admin"
    args_model = SourceIdArgs
    parameters = tool_schema(SourceIdArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`DatasourceService.set_enabled`。"""
        service = DatasourceService(ctx.repos)
        result = await service.set_enabled(
            str(kwargs["source_id"]), True, actor=ctx.user.username
        )
        return _dump(result)


@register_tool
class DisableDatasourceTool(AgentTool):
    """停用数据源（admin）。"""

    name = "disable_datasource"
    description = "停用指定数据源（采集侧下一轮即跳过该源）。"
    mutating = True
    required_role = "admin"
    args_model = SourceIdArgs
    parameters = tool_schema(SourceIdArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`DatasourceService.set_enabled`。"""
        service = DatasourceService(ctx.repos)
        result = await service.set_enabled(
            str(kwargs["source_id"]), False, actor=ctx.user.username
        )
        return _dump(result)


class SetDatasourcePriorityArgs(BaseModel):
    """``set_datasource_priority`` 入参。"""

    source_id: str = Field(min_length=1, max_length=32, description="数据源标识")
    priority: int = Field(ge=0, le=1000, description="优先级（数值越小越优先）")


@register_tool
class SetDatasourcePriorityTool(AgentTool):
    """调整数据源主备优先级（admin）。"""

    name = "set_datasource_priority"
    description = "调整数据源的优先级（数值越小越优先，用于主备切换），并写审计。"
    mutating = True
    required_role = "admin"
    args_model = SetDatasourcePriorityArgs
    parameters = tool_schema(SetDatasourcePriorityArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """更新 ``datasource_registry.priority`` 并写审计。"""
        source_id = str(kwargs["source_id"])
        priority = int(kwargs["priority"])
        hit = await ctx.repos.datasource_registry.set_priority(source_id, priority)
        if not hit:
            raise ToolError(f"数据源不存在：{source_id}", code="not_found")
        await ctx.audit("agent_set_datasource_priority", source_id, {"priority": priority})
        return {"source_id": source_id, "priority": priority}


# ------------------------------------------------------------------ 重任务


class TriggerIngestArgs(BaseModel):
    """``trigger_ingest`` 入参。"""

    task: str = Field(min_length=1, max_length=64, description="采集任务名")
    trade_date: date | None = Field(default=None, description="目标交易日；缺省为今天")


@register_tool
class TriggerIngestTool(AgentTool):
    """手动触发采集（admin，危险操作）。"""

    name = "trigger_ingest"
    description = "立即执行一个采集任务。危险操作，需人工确认后执行。"
    mutating = True
    required_role = "admin"
    requires_confirmation = True
    args_model = TriggerIngestArgs
    parameters = tool_schema(TriggerIngestArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`IngestService.trigger`（含审计、缓存失效与 WS 广播）。"""
        service = IngestService(ctx.repos)
        result = await service.trigger(
            str(kwargs["task"]), kwargs["trade_date"], actor=ctx.user.username
        )
        return _dump(result)


class RunBacktestArgs(BaseModel):
    """``run_backtest`` 入参。"""

    start: date = Field(description="回测起始日（含）")
    end: date = Field(description="回测结束日（含）")
    strategy_id: str = Field(default="dragon", max_length=64, description="策略标识")
    params: dict[str, Any] = Field(default_factory=dict, description="策略参数覆盖")


@register_tool
class RunBacktestTool(AgentTool):
    """触发回测（admin，危险操作）。"""

    name = "run_backtest"
    description = "触发一次回测（同步执行并落库）。危险操作，需人工确认后执行。"
    mutating = True
    required_role = "admin"
    requires_confirmation = True
    args_model = RunBacktestArgs
    parameters = tool_schema(RunBacktestArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ReportService.run_backtest`（含审计）。"""
        service = ReportService(ctx.repos)
        result = await service.run_backtest(
            start=kwargs["start"],
            end=kwargs["end"],
            strategy_id=str(kwargs["strategy_id"]),
            params_override=dict(kwargs["params"]),
            created_by=ctx.user.username,
        )
        return _dump(result)
