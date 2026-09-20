"""变更工具：**仅 admin** 可用，全部复用 REST API 的服务函数并写审计。

- 有对应 REST 接口的操作（策略/因子参数保存、版本回滚、策略启停、数据源启停、
  触发采集、触发回测）直接调用服务层同一方法，不重复实现业务逻辑；
- REST 未暴露的注册表维护（策略/因子定义的新增、修改、删除、版本激活）在此以
  仓储原语实现，并统一经 :attr:`ToolContext.audit` 写 ``AuditLog``。
- 危险操作（删除、版本激活/回滚、触发采集/回测）标记 ``requires_confirmation=True``，
  首轮不执行，须经 HITL 确认端点二次确认。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.tools.base import AgentTool, ToolContext, ToolError, register_tool, tool_schema
from app.models.config import FactorConfig, FactorDef, StrategyConfig, StrategyDef
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


def _dump(value: Any) -> Any:
    """把 Pydantic 响应模型转为 JSON 友好字典。"""
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


# ------------------------------------------------------------------ 策略定义


class CreateStrategyArgs(BaseModel):
    """``create_strategy`` 入参。"""

    strategy_id: str = Field(min_length=1, max_length=64, description="策略唯一标识")
    label: str = Field(min_length=1, max_length=128, description="显示名")
    version: str = Field(min_length=1, max_length=32, description="策略代码版本号")
    description: str | None = Field(default=None, description="策略说明")
    params_schema: dict[str, Any] = Field(default_factory=dict, description="参数 schema")
    gate_matrix: dict[str, Any] = Field(default_factory=dict, description="周期门控矩阵")
    enabled: bool = Field(default=True, description="是否启用")


@register_tool
class CreateStrategyTool(AgentTool):
    """新建策略定义（admin）。"""

    name = "create_strategy"
    description = "新建一条策略定义（含参数 schema 与门控矩阵）。已存在则报错。"
    mutating = True
    required_role = "admin"
    args_model = CreateStrategyArgs
    parameters = tool_schema(CreateStrategyArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """写入 ``strategy_defs`` 并写审计。"""
        strategy_id = str(kwargs["strategy_id"])
        if await ctx.repos.strategy_defs.get(strategy_id) is not None:
            raise ToolError(f"策略已存在：{strategy_id}", code="already_exists")
        await ctx.repos.strategy_defs.upsert(
            strategy_id,
            str(kwargs["label"]),
            str(kwargs["version"]),
            dict(kwargs["params_schema"]),
            dict(kwargs["gate_matrix"]),
            description=kwargs["description"],
            enabled=bool(kwargs["enabled"]),
        )
        await ctx.audit(
            "agent_create_strategy",
            strategy_id,
            {"label": kwargs["label"], "version": kwargs["version"]},
        )
        return {"strategy_id": strategy_id, "created": True}


class UpdateStrategyArgs(BaseModel):
    """``update_strategy`` 入参（未给出的字段保持不变）。"""

    strategy_id: str = Field(min_length=1, max_length=64, description="策略唯一标识")
    label: str | None = Field(default=None, max_length=128, description="显示名")
    version: str | None = Field(default=None, max_length=32, description="策略代码版本号")
    description: str | None = Field(default=None, description="策略说明")
    params_schema: dict[str, Any] | None = Field(default=None, description="参数 schema")
    gate_matrix: dict[str, Any] | None = Field(default=None, description="周期门控矩阵")
    enabled: bool | None = Field(default=None, description="是否启用")


@register_tool
class UpdateStrategyTool(AgentTool):
    """修改策略定义（admin）。"""

    name = "update_strategy"
    description = "修改策略定义的部分字段（未给出的字段保持原值）。"
    mutating = True
    required_role = "admin"
    args_model = UpdateStrategyArgs
    parameters = tool_schema(UpdateStrategyArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """读取现有定义后合并覆盖写，并写审计。"""
        strategy_id = str(kwargs["strategy_id"])
        existing = await ctx.repos.strategy_defs.get(strategy_id)
        if existing is None:
            raise ToolError(f"策略不存在：{strategy_id}", code="not_found")
        await ctx.repos.strategy_defs.upsert(
            strategy_id,
            str(kwargs["label"] if kwargs["label"] is not None else existing.label),
            str(kwargs["version"] if kwargs["version"] is not None else existing.version),
            dict(
                kwargs["params_schema"]
                if kwargs["params_schema"] is not None
                else existing.params_schema
            ),
            dict(
                kwargs["gate_matrix"]
                if kwargs["gate_matrix"] is not None
                else existing.gate_matrix
            ),
            description=(
                kwargs["description"] if kwargs["description"] is not None else existing.description
            ),
            enabled=bool(kwargs["enabled"] if kwargs["enabled"] is not None else existing.enabled),
        )
        await ctx.audit("agent_update_strategy", strategy_id, {"fields": sorted(kwargs)})
        return {"strategy_id": strategy_id, "updated": True}


class DeleteStrategyArgs(BaseModel):
    """``delete_strategy`` 入参。"""

    strategy_id: str = Field(min_length=1, max_length=64, description="策略唯一标识")


@register_tool
class DeleteStrategyTool(AgentTool):
    """删除策略定义及其全部参数版本（admin，危险操作）。"""

    name = "delete_strategy"
    description = "删除策略定义及其全部参数配置版本。危险操作，需人工确认后执行。"
    mutating = True
    required_role = "admin"
    requires_confirmation = True
    args_model = DeleteStrategyArgs
    parameters = tool_schema(DeleteStrategyArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """删除 ``strategy_defs`` 与 ``strategy_configs`` 对应行，并写审计。"""
        strategy_id = str(kwargs["strategy_id"])
        if await ctx.repos.strategy_defs.get(strategy_id) is None:
            raise ToolError(f"策略不存在：{strategy_id}", code="not_found")
        configs = await ctx.repos.strategy_configs.delete_where(
            StrategyConfig, StrategyConfig.strategy_id == strategy_id
        )
        await ctx.repos.strategy_defs.delete_where(
            StrategyDef, StrategyDef.strategy_id == strategy_id
        )
        await ctx.audit("agent_delete_strategy", strategy_id, {"configs_removed": configs})
        return {"strategy_id": strategy_id, "deleted": True, "configs_removed": configs}


# ------------------------------------------------------------------ 因子定义


class CreateFactorArgs(BaseModel):
    """``create_factor`` 入参。"""

    factor_id: str = Field(min_length=1, max_length=64, description="因子唯一标识")
    label: str = Field(min_length=1, max_length=128, description="显示名")
    category: str = Field(min_length=1, max_length=32, description="因子类别")
    description: str | None = Field(default=None, description="因子说明")
    params_schema: dict[str, Any] = Field(default_factory=dict, description="参数 schema")
    enabled: bool = Field(default=True, description="是否启用")


@register_tool
class CreateFactorTool(AgentTool):
    """新建因子定义（admin）。"""

    name = "create_factor"
    description = "新建一条因子定义（含参数 schema 与档位）。已存在则报错。"
    mutating = True
    required_role = "admin"
    args_model = CreateFactorArgs
    parameters = tool_schema(CreateFactorArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """写入 ``factor_defs`` 并写审计。"""
        factor_id = str(kwargs["factor_id"])
        if await ctx.repos.factor_defs.get(factor_id) is not None:
            raise ToolError(f"因子已存在：{factor_id}", code="already_exists")
        await ctx.repos.factor_defs.upsert(
            factor_id,
            str(kwargs["label"]),
            str(kwargs["category"]),
            dict(kwargs["params_schema"]),
            description=kwargs["description"],
            enabled=bool(kwargs["enabled"]),
        )
        await ctx.audit("agent_create_factor", factor_id, {"label": kwargs["label"]})
        return {"factor_id": factor_id, "created": True}


class UpdateFactorArgs(BaseModel):
    """``update_factor`` 入参（未给出的字段保持不变）。"""

    factor_id: str = Field(min_length=1, max_length=64, description="因子唯一标识")
    label: str | None = Field(default=None, max_length=128, description="显示名")
    category: str | None = Field(default=None, max_length=32, description="因子类别")
    description: str | None = Field(default=None, description="因子说明")
    params_schema: dict[str, Any] | None = Field(default=None, description="参数 schema")
    enabled: bool | None = Field(default=None, description="是否启用")


@register_tool
class UpdateFactorTool(AgentTool):
    """修改因子定义（admin）。"""

    name = "update_factor"
    description = "修改因子定义的部分字段（未给出的字段保持原值）。"
    mutating = True
    required_role = "admin"
    args_model = UpdateFactorArgs
    parameters = tool_schema(UpdateFactorArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """读取现有定义后合并覆盖写，并写审计。"""
        factor_id = str(kwargs["factor_id"])
        existing = await ctx.repos.factor_defs.get(factor_id)
        if existing is None:
            raise ToolError(f"因子不存在：{factor_id}", code="not_found")
        await ctx.repos.factor_defs.upsert(
            factor_id,
            str(kwargs["label"] if kwargs["label"] is not None else existing.label),
            str(kwargs["category"] if kwargs["category"] is not None else existing.category),
            dict(
                kwargs["params_schema"]
                if kwargs["params_schema"] is not None
                else existing.params_schema
            ),
            description=(
                kwargs["description"] if kwargs["description"] is not None else existing.description
            ),
            enabled=bool(kwargs["enabled"] if kwargs["enabled"] is not None else existing.enabled),
        )
        await ctx.audit("agent_update_factor", factor_id, {"fields": sorted(kwargs)})
        return {"factor_id": factor_id, "updated": True}


class DeleteFactorArgs(BaseModel):
    """``delete_factor`` 入参。"""

    factor_id: str = Field(min_length=1, max_length=64, description="因子唯一标识")


@register_tool
class DeleteFactorTool(AgentTool):
    """删除因子定义及其全部参数版本（admin，危险操作）。"""

    name = "delete_factor"
    description = "删除因子定义及其全部参数配置版本。危险操作，需人工确认后执行。"
    mutating = True
    required_role = "admin"
    requires_confirmation = True
    args_model = DeleteFactorArgs
    parameters = tool_schema(DeleteFactorArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """删除 ``factor_defs`` 与 ``factor_configs`` 对应行，并写审计。"""
        factor_id = str(kwargs["factor_id"])
        if await ctx.repos.factor_defs.get(factor_id) is None:
            raise ToolError(f"因子不存在：{factor_id}", code="not_found")
        configs = await ctx.repos.factor_configs.delete_where(
            FactorConfig, FactorConfig.factor_id == factor_id
        )
        await ctx.repos.factor_defs.delete_where(FactorDef, FactorDef.factor_id == factor_id)
        await ctx.audit("agent_delete_factor", factor_id, {"configs_removed": configs})
        return {"factor_id": factor_id, "deleted": True, "configs_removed": configs}


# ------------------------------------------------------------------ 参数配置


class UpdateStrategyConfigArgs(BaseModel):
    """``update_strategy_config`` 入参。"""

    strategy_id: str = Field(min_length=1, max_length=64, description="策略标识")
    params: dict[str, Any] = Field(description="参数键值对（整包覆盖）")
    note: str | None = Field(default=None, max_length=255, description="变更说明")


@register_tool
class UpdateStrategyConfigTool(AgentTool):
    """保存并启用策略参数（admin）。"""

    name = "update_strategy_config"
    description = "保存并启用一份新的策略参数版本（新建版本并置 active，历史版本归档）。"
    mutating = True
    required_role = "admin"
    args_model = UpdateStrategyConfigArgs
    parameters = tool_schema(UpdateStrategyConfigArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ConfigService.save_strategy_config`（含审计与缓存失效）。"""
        service = ConfigService(ctx.repos)
        result = await service.save_strategy_config(
            str(kwargs["strategy_id"]),
            dict(kwargs["params"]),
            note=kwargs["note"],
            actor=ctx.user.username,
        )
        return _dump(result)


class UpdateFactorConfigArgs(BaseModel):
    """``update_factor_config`` 入参。"""

    factor_id: str = Field(min_length=1, max_length=64, description="因子标识")
    params: dict[str, Any] = Field(description="参数键值对（整包覆盖）")
    note: str | None = Field(default=None, max_length=255, description="变更说明")


@register_tool
class UpdateFactorConfigTool(AgentTool):
    """保存并启用因子参数（admin）。"""

    name = "update_factor_config"
    description = "保存并启用一份新的因子参数版本（新建版本并置 active，历史版本归档）。"
    mutating = True
    required_role = "admin"
    args_model = UpdateFactorConfigArgs
    parameters = tool_schema(UpdateFactorConfigArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ConfigService.save_factor_config`（含审计与缓存失效）。"""
        service = ConfigService(ctx.repos)
        result = await service.save_factor_config(
            str(kwargs["factor_id"]),
            dict(kwargs["params"]),
            note=kwargs["note"],
            actor=ctx.user.username,
        )
        return _dump(result)


class ActivateConfigVersionArgs(BaseModel):
    """``activate_config_version`` 入参。"""

    kind: Literal["strategy", "factor"] = Field(description="配置归属：strategy 或 factor")
    owner_id: str = Field(min_length=1, max_length=64, description="策略或因子标识")
    version: int = Field(ge=1, description="要激活的版本号")


@register_tool
class ActivateConfigVersionTool(AgentTool):
    """把历史版本置为 active（admin，危险操作）。"""

    name = "activate_config_version"
    description = "把指定版本置为 active（其余版本归档）。危险操作，需人工确认后执行。"
    mutating = True
    required_role = "admin"
    requires_confirmation = True
    args_model = ActivateConfigVersionArgs
    parameters = tool_schema(ActivateConfigVersionArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """按 ``kind`` 分派到对应版本化仓储的 ``activate``，并写审计。"""
        owner_id = str(kwargs["owner_id"])
        version = int(kwargs["version"])
        row: Any
        if kwargs["kind"] == "strategy":
            row = await ctx.repos.strategy_configs.activate(owner_id, version)
        else:
            row = await ctx.repos.factor_configs.activate(owner_id, version)
        await ctx.audit(
            "agent_activate_config_version",
            owner_id,
            {"kind": kwargs["kind"], "version": version},
        )
        return {
            "owner_id": owner_id,
            "kind": kwargs["kind"],
            "version": int(row.version),
            "status": str(row.status),
        }


class RollbackConfigVersionArgs(BaseModel):
    """``rollback_config_version`` 入参。"""

    kind: Literal["strategy", "factor"] = Field(description="配置归属：strategy 或 factor")
    owner_id: str = Field(min_length=1, max_length=64, description="策略或因子标识")
    version: int = Field(ge=1, description="要回滚到的历史版本号")


@register_tool
class RollbackConfigVersionTool(AgentTool):
    """回滚到历史版本（admin，危险操作）。"""

    name = "rollback_config_version"
    description = "回滚到指定历史版本（以历史内容新建 active 版本）。危险操作，需人工确认后执行。"
    mutating = True
    required_role = "admin"
    requires_confirmation = True
    args_model = RollbackConfigVersionArgs
    parameters = tool_schema(RollbackConfigVersionArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """调用 :meth:`ConfigService.rollback_strategy` / ``rollback_factor``。"""
        service = ConfigService(ctx.repos)
        owner_id = str(kwargs["owner_id"])
        version = int(kwargs["version"])
        if kwargs["kind"] == "strategy":
            result = await service.rollback_strategy(owner_id, version, actor=ctx.user.username)
        else:
            result = await service.rollback_factor(owner_id, version, actor=ctx.user.username)
        return _dump(result)


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
