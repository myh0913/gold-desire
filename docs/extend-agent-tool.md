# 扩展指南：新增 Agent 工具

> 目标：为内置 Agent 增加「可被模型调用的能力」，同时保住三条安全红线——
> 服务端权限强制、白名单结构化查询、全量审计。

## 1. 工具协议（`app/agent/tools/base.py`）

```python
class MyTool(AgentTool):
    name = "query_mything"                    # 全局唯一，注册期查重
    description = "查询 XX，返回 YY"           # 作为 LLM 工具描述，写清入参语义
    parameters = tool_schema(MyArgs)          # Pydantic 模型 → OpenAI JSON Schema
    mutating = False                          # 只读工具；True = 变更工具（仅 admin）
    required_role = None                      # 可选：限定角色（如 "analyst"）
    requires_confirmation = False             # True = 危险操作，走 HITL 二次确认

    async def run(self, ctx: ToolContext, **kwargs) -> dict[str, Any]:
        args = MyArgs.model_validate(kwargs)   # 参数经 Pydantic 校验
        ...                                     # 经 ctx.repos（服务层/仓储）取数
        await ctx.audit("agent_tool_mything", target, detail)  # 变更类必须审计
        return {...}                            # 回给模型的结构化结果
```

- 注册：`@register_tool` 装饰器（在 `agent/tools/__init__.py` 聚合 import）；
  重复 `name` 启动即报错。
- `ctx`（`ToolContext`）携带：已认证用户、仓储容器、配置、会话 ID、缓存、
  审计钩子——**工具不得自行开数据库会话或触达数据源**。

## 2. 只读 vs 变更

| | 只读工具 | 变更工具 |
| --- | --- | --- |
| 声明 | `mutating = False` | `mutating = True` |
| 可见角色 | 所有登录角色 | **仅 admin**（双重强制，见下） |
| 典型 | `read.py`：query_stocks / query_daily_bars / query_minute_bars / query_limit_up_pool / query_ladder / query_sentiment / query_theme / query_newsflash / query_monitor / query_advice / query_backtest / list_strategies / get_strategy / list_factors / get_factor / get_factor_effectiveness / get_ingest_health / list_datasources / query_table | `mutate.py`：create/update/delete_strategy、create/update/delete_factor、配置版本启用/回滚、数据源启停与优先级、触发采集/回测/沙箱 |
| HITL | 否 | 删除、启用/回滚、触发重任务须 `requires_confirmation = True` |

### 服务端双重强制（`app/agent/permissions.py`）

1. **清单过滤** `available_tools(user)`：非 admin 的工具清单**不含任何变更工具**
   ——模型与前端根本看不到（隐藏是体验，不是边界）；
2. **执行前二次校验** `authorize(tool, user, ...)`：即使构造非法请求绕过清单，
   执行前仍按服务端角色再判一次，越权即 403 并写
   `AuditLog(action="agent_tool_denied")`。

### HITL（人工介入）

`requires_confirmation=True` 的工具首轮**不执行**：在缓存中登记一条绑定
「会话 + 用户」的一次性令牌（TTL 300s，`agent:confirm:*`），前端弹二次确认框，
用户确认后经 `POST /api/agent/sessions/{id}/confirm` 携令牌真正执行——
他人拿到令牌也无法复用（会话/用户不匹配即拒绝）。

## 3. 数据查询工具的白名单约束

`query_table`（`agent/tools/query_table.py`）：

- 只接受 `TABLE_WHITELIST` 中的表名（由 ORM 模型派生）+ 结构化过滤条件
  （字段/操作/值），**不接受裸 SQL 字符串**；
- 返回自动分页截断，禁止无界返回；
- 新表要开放给 Agent：把 ORM 模型加进 `TABLE_WHITELIST` 即可（仍零 SQL 暴露）。

## 4. 审计与预算

- **审计**：会话（`agent_sessions`）、消息（`agent_messages`）、每次工具调用
  （`agent_tool_calls`：入参、结果摘要、耗时、是否被拒）全量落库，
  可还原「谁、何时、哪个会话、调了哪个工具、结果如何」；
- **预算**：单会话最大轮数（`AGENT_MAX_TURNS`）、工具调用上限
  （`AGENT_MAX_TOOL_CALLS`）、单次模型超时（`AGENT_TIMEOUT_SECONDS`）；
  超限优雅终止并提示；
- **降级**：模型不可用/超时转为明确错误提示，不阻塞主应用。

## 5. Skill 层（可选）

预置技能（`app/agent/skills.py`）= 提示词 + 工具编排，现有四个：
「今日涨停结构分析」「策略参数对比」「某日无候选原因排查」「数据健康巡检」。
新技能只需在 `_SKILLS` 增加一项 `AgentSkill(name, description, system_prompt,
tools, output_format)`，`tools` 引用已注册的工具名。

## 6. 接入步骤

1. `app/agent/tools/read.py`（只读）或 `mutate.py`（变更）中新增工具类；
2. 定义 Pydantic 入参模型并 `tool_schema()` 导出参数描述；
3. `agent/tools/__init__.py` 聚合 import；变更工具补 `requires_confirmation` 与
   `ctx.audit(...)`；
4. 验证：`pytest tests/test_agent.py tests/test_agent_security.py`
   （含非 admin 清单过滤、越权 403 + 审计、HITL 令牌绑定）。
