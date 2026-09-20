# gold-desire 文档索引

| 文档 | 文件 | 内容概要 |
| --- | --- | --- |
| 架构说明 | [architecture.md](architecture.md) | 分层与依赖方向（core → db → repositories → services → api + datasources/factors/strategies/engine/ingest/agent 五域）、关键设计决策（能力契约+声明式映射、策略插件+门控下沉、采集/服务分离、两级缓存、回放防未来函数）、ASCII 数据流图 |
| 数据流说明 | [data-flow.md](data-flow.md) | 上游 → 采集标准化 → 分区表 → 缓存 → 读 API → 前端全链路；ingest 窗口/幂等/保留策略；stale 语义；WS 通道协议 |
| 扩展指南·新增数据源 | [extend-datasource.md](extend-datasource.md) | provider + 声明式 mapping（Python/YAML）+ 能力顺序登记；fake provider 与 hithink 真实示例摘录；`PUT /api/datasources/prefs`（set_capability_order）主备热切换与离线回落 fake 行为 |
| 扩展指南·新增策略 | [extend-strategy.md](extend-strategy.md) | plugins/ 目录结构、BaseStrategy 协议、gate_matrix 下沉、phases ↔ 钩子、params_schema 与参数命名空间隔离、复用 sell_rules/portfolio；引用 `strategies/examples/` 与龙回头实现 |
| 扩展指南·新增因子 | [extend-factor.md](extend-factor.md) | 因子注册（factor_id / 参数 schema / 档位）、参数版本化（draft/active/archived、热生效、回滚）、effectiveness 统计（档位 × A/B/C 三段） |
| 扩展指南·新增 Agent 工具 | [extend-agent-tool.md](extend-agent-tool.md) | 只读/变更分级、服务端双重权限强制、HITL 二次确认、白名单表结构化查询、审计与调用预算、Skill 层 |
| 部署运维 | [deploy-ops.md](deploy-ops.md) | Compose 部署步骤（两种前端构建路径）、`.env` 全量说明、启停/升级/备份恢复、日志、扩容 PG/Redis、常见故障排查表、性能压测方法（Task 17 脚本入口） |

## 相关入口

- 后端工程与命令：`backend/README.md`
- 部署编排资产：`deploy/`（docker-compose.yml、compose.host-build.yml、nginx.conf）与根 `.env.example`
- 运维脚本：`scripts/`（deploy/start/stop/restart/upgrade/backup/restore）
- 策略基线文档：`../readme.md`
