# gold-desire

A 股短线四策略实盘辅助系统：多数据源采集 → 因子计算 → 策略判定 → 前端展示，全链路自动运行。

内置策略（`app/strategies/plugins/`）：**龙回头**（dragon）、**首板低吸**（firstboard_dip）、**连板**（lianban）、**竞价抢筹**（auction_grab）。建议实时推送，复盘页按策略回溯胜率与收益。

> 龙回头策略本身的设计（两路买点、门槛、加分项、止损卖出规则）见 [docs/strategy-dragon.md](docs/strategy-dragon.md)（原根目录 README，即代码注释中引用的 "readme §x"）。

## 架构

```
backend/  FastAPI + SQLAlchemy + PostgreSQL
  app/api/          路由层（REST + WebSocket）
  app/services/     业务逻辑
  app/repositories/ 数据访问（幂等 upsert）
  app/models/       ORM 模型（行情/派生/配置分月表）
  app/datasources/  数据源抽象：providers（取数）+ mappings（字段映射）+ contracts（契约校验）
  app/ingest/       采集调度器（窗口制，systemd 常驻）
  app/factors/      因子注册表 + 内置因子
  app/strategies/   策略协议 + 插件（plugins/：dragon 龙回头 / firstboard_dip 首板低吸 / lianban 连板 / auction_grab 竞价抢筹）
  app/engine/       样本构造 / 卖出撮合 / 组合模拟
  app/agent/        内置 AI Agent（工具调用）
frontend/ Vite + React（TanStack Query + WS 实时刷新），构建产物 dist 由 Caddy 直接 serve
scripts/   运维脚本（部署/启停/升级/备份）
deploy/    Docker Compose 编排（本机为 systemd 直跑，不用 Docker）
docs/      架构 / 数据流 / 部署 / 扩展文档
```

## 数据源

| source_id | 提供方 | 主要能力 |
|---|---|---|
| xuangutong | 选股宝 | 涨停池（7 池型）、题材排名/个股、快讯、情绪 |
| hithink | 同花顺 | 日线、天梯、交易日历、涨停池（备用源） |
| eltdx | 通达信行情 | 分钟线、开盘撮合、竞价时序 |
| eastmoney | 东方财富 | 监管名单（重点监控/异常波动） |

> eltdx 为可选依赖（开盘撮合/竞价时序类任务的唯一真实源），生产 venv 需安装：
> `cd backend && .venv/bin/pip install ".[eltdx]"`。未安装时采集侧**显式失败不静默降级**，
> 仅影响撮合/竞价任务（J1 竞价抢筹、开盘撮合 ingest），其余策略不受影响。

## 运行

生产部署见 [Deploy.md](Deploy.md) 与 [docs/deploy-ops.md](docs/deploy-ops.md)。要点：

- systemd 单元：`gold-desire-api`（uvicorn :8001）、`gold-desire-worker`（采集调度器）
- Caddy 按 `/gd` 前缀反代，前端静态文件直 serve dist
- PostgreSQL 库名 `gold_desire`，Redis 本机 127.0.0.1
- 采集窗口：竞价 09:25 / 盘中 / 尾盘 14:45 / 盘后 17:00 + 全天日历任务；其余时段安静是设计
- 备份：`scripts/backup-host.sh`（cron 每天 03:17，保留 14 份，输出 ~/backups/gold-desire/）

## 开发

```bash
cd backend && .venv/bin/pytest        # 测试
cd backend && .venv/bin/alembic upgrade head   # 迁移（部署提示要求时）
cd frontend && npm run build          # 前端构建（Caddy 直 serve dist）
```

扩展指引：[docs/extend-strategy.md](docs/extend-strategy.md)（新策略）、[docs/extend-datasource.md](docs/extend-datasource.md)（新数据源）。
