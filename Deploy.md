# gold-desire 部署入口（Deploy.md）

> **TL;DR**：在干净 Linux 服务器上跑 `scripts/deploy.sh`，再按 `docs/deploy-ops.md`
> 准备首个管理员即可。本文件是顶层入口指针，**完整流程在下面两处**。

## 1. 资产位置

| 类型 | 路径 |
| --- | --- |
| 编排 | `deploy/docker-compose.yml`（默认 build-in-image）+ `deploy/compose.host-build.yml`（override） |
| 站点 | `deploy/nginx.conf`（SPA 托管 + `/api` 反代 + SSE/WS + 安全响应头） |
| 后端镜像 | `backend/Dockerfile`（api / worker / migrate 共用，`python:3.12-slim`） |
| 前端镜像 | `frontend/Dockerfile`（node:20-alpine 构建 → nginx:1.27-alpine 托管） |
| 配置模板 | 根 `.env.example`（compose 插值 + Settings 共用） / `backend/.env.example`（Settings 字段全集） |
| 运维脚本 | `scripts/{deploy,start,stop,restart,upgrade,backup,restore}.sh` + `scripts/lib.sh` |
| 文档 | `docs/`（architecture / data-flow / deploy-ops / extend-*） |

## 2. 服务矩阵（Compose）

| 服务 | 镜像 / 构建 | 端口（宿主机→容器） | 用途 |
| --- | --- | --- | --- |
| postgres | `postgres:16-alpine` | 仅内网 | 唯一权威存储；卷 `pg_data`；`pg_isready` 健康检查 |
| redis | `redis:7-alpine` | 仅内网 | L1 缓存 + 限流 + HITL 令牌；AOF + `noeviction`；卷 `redis_data` |
| api | `gold-desire-backend`（来自 `backend/Dockerfile`） | 仅内网（nginx 反代） | FastAPI；启动先 `alembic upgrade head` 再 `uvicorn` |
| worker | 同 api 镜像 | — | `python -m app.ingest.scheduler`（采集 / 窗口 / 保留） |
| nginx | `gold-desire-frontend`（来自 `frontend/Dockerfile`） | 80 / 443（可改） | 唯一对外入口；SPA + `/api` + `/ws` + SSE 透传 |
| migrate | 同 api 镜像（`profiles: ["tools"]`） | — | 一次性 `alembic upgrade head`（亦由 api 启动命令幂等执行） |

## 3. 前端两种构建路径

- **路径 A：build-in-image（默认）** —— `frontend/Dockerfile` 内 `node:20-alpine` 构建
  `dist` → `nginx:1.27-alpine` 托管；`docker compose build` 一次解决。
- **路径 B：host-build（override）** —— 宿主机 `cd frontend && npm ci && npm run build`
  产出 `dist/`，经 `deploy/compose.host-build.yml`（依赖 Compose v2.24+ `!reset` 标签）
  用官方 `nginx:1.27-alpine` 挂载 `../frontend/dist:/usr/share/nginx/html:ro`。

切换：`GD_COMPOSE_FILES="deploy/docker-compose.yml deploy/compose.host-build.yml" scripts/restart.sh env`。

## 4. 迁移策略

采用**入口式（api 容器 command）**：`alembic upgrade head && exec uvicorn ...`，
每次启动先补齐 schema 再监听；`scripts/upgrade.sh` 另用 `docker compose run --rm api
alembic upgrade head` 在升级前显式迁移（双保险，幂等安全）。

## 5. 三步上手

```bash
git clone <repo> && cd gold-desire
cp .env.example .env && vim .env      # 改 JWT_SECRET / POSTGRES_PASSWORD / AGENT_MODEL_API_KEY
scripts/deploy.sh                      # 校验 .env → 构建 → 启动 → 等 /ready
# 登录前创建首个管理员见 docs/deploy-ops.md「§2.1 首个管理员」
```

## 6. 完整文档

- 部署运维（必读）：[docs/deploy-ops.md](docs/deploy-ops.md)
- 架构 & 设计决策：[docs/architecture.md](docs/architecture.md)
- 数据流 & WS 协议：[docs/data-flow.md](docs/data-flow.md)
- 扩展指南：新增数据源 / 策略 / 因子 / Agent 工具 —— [docs/README.md](docs/README.md)