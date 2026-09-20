"""``query_table``：**白名单表 + 结构化过滤条件**的受控查询工具。

安全边界（spec：数据查询工具 SHALL 只接受白名单表 + 结构化过滤条件，
SHALL NOT 接受裸 SQL 字符串）：

1. **表名白名单**：仅允许 :data:`TABLE_WHITELIST` 中的表（由 ORM 模型派生），
   含认证/审计/原始留档的表（``users`` / ``roles`` / ``audit_logs`` /
   ``raw_responses`` 等）**一律不在白名单**，避免泄露凭证与审计内容。
2. **拒绝裸 SQL**：递归扫描全部字符串入参，命中 ``;`` / ``--`` / ``/*`` 或 SQL
   关键字即拒绝，绝不进入查询构造。
3. **仅经 SQLAlchemy 构造**：过滤条件由列对象 + 运算符拼装（``eq`` / ``gt`` /
   ``like`` …），**不存在任何字符串拼接 SQL 的路径**；字段名必须真实存在于模型列。
4. **强制有界**：``limit`` 收敛到 ``[1, page_size_max]``。
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.agent.tools.base import AgentTool, ToolContext, ToolError, register_tool, tool_schema
from app.models.config import (
    DatasourceRegistry,
    FactorConfig,
    FactorDef,
    StrategyConfig,
    StrategyDef,
)
from app.models.derived import AdviceReport, BacktestRun, IngestJob
from app.models.market import (
    DailyBar,
    LadderRow,
    LimitUpPool,
    MarketSentiment,
    MinuteBar,
    MonitorStock,
    NewsFlash,
    Theme,
    ThemeStock,
)

__all__ = ["TABLE_WHITELIST", "QueryTableTool"]

#: 表名白名单（由 ORM 模型派生；不含用户/角色/审计/原始留档表）。
TABLE_WHITELIST: dict[str, type[Any]] = {
    model.__tablename__: model
    for model in (
        DailyBar,
        MinuteBar,
        LimitUpPool,
        MarketSentiment,
        NewsFlash,
        Theme,
        ThemeStock,
        MonitorStock,
        LadderRow,
        AdviceReport,
        BacktestRun,
        IngestJob,
        StrategyDef,
        StrategyConfig,
        FactorDef,
        FactorConfig,
        DatasourceRegistry,
    )
}

#: 裸 SQL 特征：语句分隔符、注释符与常见 SQL 关键字。
_RAW_SQL_PATTERN = re.compile(
    r"(;|--|/\*)"
    r"|\b(select|insert|update|delete|drop|alter|create|truncate|union|exec|execute"
    r"|grant|revoke|attach|pragma|replace|call|into|from|where)\b",
    re.IGNORECASE,
)

_OPERATORS: dict[str, str] = {
    "eq": "__eq__",
    "ne": "__ne__",
    "gt": "__gt__",
    "gte": "__ge__",
    "lt": "__lt__",
    "lte": "__le__",
}


class TableFilter(BaseModel):
    """单个结构化过滤条件（拒绝多余字段，如 ``sql``）。"""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="字段名（须为该表的真实列）")
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "like"] = Field(
        default="eq", description="比较运算符"
    )
    value: Any = Field(default=None, description="比较值（in 需传数组）")


class QueryTableArgs(BaseModel):
    """``query_table`` 入参（**没有** raw SQL 字段，且拒绝任何多余字段）。"""

    model_config = ConfigDict(extra="forbid")

    table: str = Field(description="白名单表名，如 daily_bars")
    filters: list[TableFilter] = Field(default_factory=list, description="结构化过滤条件（AND）")
    order_by: str | None = Field(default=None, description="排序列名（须为真实列）")
    order: Literal["asc", "desc"] = Field(default="desc", description="排序方向")
    limit: int = Field(default=20, ge=1, le=200, description="返回条数上限（≤200）")


def _iter_strings(value: Any) -> Iterator[str]:
    """递归枚举值中的所有字符串。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _iter_strings(item)


def _reject_raw_sql(payload: Mapping[str, Any]) -> None:
    """递归检查入参中的字符串，命中裸 SQL 特征即拒绝。

    Raises:
        ToolError: 命中语句分隔符/注释符/SQL 关键字。
    """
    for text in _iter_strings(payload):
        hit = _RAW_SQL_PATTERN.search(text)
        if hit is not None:
            raise ToolError(
                "query_table 仅接受白名单表 + 结构化过滤条件，不接受裸 SQL 字符串",
                code="raw_sql_rejected",
                detail={"pattern": hit.group(0)},
            )


def _jsonify(value: Any) -> Any:
    """把 ORM 标量转为 JSON 友好值。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


@register_tool
class QueryTableTool(AgentTool):
    """受控的结构化查询（白名单表 + 过滤条件 + 排序 + 限量）。"""

    name = "query_table"
    description = (
        "按白名单表做结构化查询：传表名、结构化过滤条件（field/op/value）、排序列与条数上限。"
        "不接受 SQL 语句；表名与字段名均须为白名单内的真实表/列。"
    )
    args_model = QueryTableArgs
    parameters = tool_schema(QueryTableArgs)

    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """构造并执行参数化查询。

        Raises:
            ToolError: 命中裸 SQL、表不在白名单、字段非法或 in 值不是数组。
        """
        _reject_raw_sql(kwargs)

        table = str(kwargs["table"])
        model = TABLE_WHITELIST.get(table)
        if model is None:
            raise ToolError(
                f"表不在白名单内：{table}",
                code="table_not_allowed",
                detail={"allowed": sorted(TABLE_WHITELIST)},
            )

        columns = {column.name: column for column in model.__table__.columns}
        stmt = select(model)
        for item in kwargs["filters"]:
            field = str(item["field"])
            column = columns.get(field)
            if column is None:
                raise ToolError(
                    f"字段不存在于表 {table}：{field}",
                    code="unknown_field",
                    detail={"allowed": sorted(columns)},
                )
            stmt = stmt.where(_condition(model, field, str(item["op"]), item.get("value")))

        order_by = kwargs["order_by"]
        if order_by:
            if str(order_by) not in columns:
                raise ToolError(
                    f"排序字段不存在于表 {table}：{order_by}",
                    code="unknown_field",
                    detail={"allowed": sorted(columns)},
                )
            column = getattr(model, str(order_by))
            stmt = stmt.order_by(column.desc() if kwargs["order"] == "desc" else column.asc())

        limit = min(max(1, int(kwargs["limit"])), max(1, ctx.settings.page_size_max))
        result = await ctx.repos.session.execute(stmt.limit(limit))
        rows = [
            {name: _jsonify(getattr(row, name)) for name in columns}
            for row in result.scalars().all()
        ]
        return {"table": table, "count": len(rows), "limit": limit, "items": rows}


def _condition(model: type[Any], field: str, op: str, value: Any) -> Any:
    """由列对象 + 运算符构造过滤条件（参数化，无字符串拼接）。"""
    column = getattr(model, field)
    if op == "in":
        if not isinstance(value, (list, tuple)):
            raise ToolError(
                f"op=in 的 value 必须是数组：{field}", code="invalid_arguments"
            )
        return column.in_(list(value))
    if op == "like":
        return column.like(str(value))
    operator = _OPERATORS.get(op)
    if operator is None:  # pragma: no cover - pydantic Literal 已拦截
        raise ToolError(f"不支持的运算符：{op}", code="invalid_arguments")
    return getattr(column, operator)(value)
