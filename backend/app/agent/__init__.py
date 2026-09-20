"""内置 Agent（OpenAI 兼容 + 工具层 + Skill 层 + 审计）。

模块划分：

- :mod:`app.agent.client`：OpenAI 兼容模型客户端与结构化错误映射（可注入替身）；
- :mod:`app.agent.tools`：工具层（只读工具 + 仅 admin 的变更工具 + 白名单结构化查询）；
- :mod:`app.agent.permissions`：服务端强制的角色过滤、执行前二次校验与 HITL 确认；
- :mod:`app.agent.skills`：预置技能（提示词 + 工具子集 + 输出格式）；
- :mod:`app.agent.loop`：多轮工具调用循环（预算控制、流式事件、上下文裁剪、全量审计）。

对外的 HTTP 入口见 :mod:`app.api.agent`，编排见 :mod:`app.services.agent_service`。
"""
