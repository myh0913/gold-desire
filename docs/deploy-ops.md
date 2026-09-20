# 部署运维手册

> 面向 Linux 服务器单机部署（Docker Compose v2）。架构与数据流见
> [architecture.md](architecture.md) / [data-flow.md](data-flow.md)。

## 1. 前置条件

- Linux x86_64，2C4G 起步（推荐 4C8G），50G+ 磁盘；
- Docker Engine 24+ 与 Docker Compose v2.24+（`docker compose version` 验证；
  v2.24 起支持 override 文件的 `!reset` 标签，宿主机构建路径依赖它）；
- `curl`（就绪探测用）、`git`（升级脚本用，可选）；
- 开放 80/443（对外），其余端口全部只在内网/容器网络。

## 2. 首次部署

```bash
git clone <repo> && cd gold-desire
cp .env.example .env && vim .env      # 填【必改】项（见 §4）
scripts/deploy.sh
```

`deploy.sh` 流程：检查 docker → 生成/校验 `.env`（敏感项未改会警告）→
`docker compose build` → `up -d` → 轮询 `http://127.0.0.1/ready` → 打印管理员
初始化提示。成功标志：`/ready` 返回 `{"status":"ready",...}`，五个容器全部
healthy/up。

### 2.1 首个管理员

系统无内置 bootstrap（注册默认角色为 viewer，admin 只能来自邀请码，而邀请码
又需要 admin 创建），首次部署在服务器上执行一次性容器内命令：

```bash
docker compose -p gold-desire exec -T api python - <<'PY'
import asyncio
from app.core.security import hash_password
from app.db.session import get_session_factory
from app.models.auth import User
from app.repositories import Repositories
from app.repositories.auth import UserRepository
from app.services.role_service import RoleService

async def main() -> None:
    async with get_session_factory()() as session:
        await RoleService(Repositories.build(session)).ensure_builtin_roles()
        if await UserRepository(session).get_by_username("admin") is None:
            session.add(User(username="admin",
                             password_hash=hash_password("Admin123-change"),
                             role="admin"))
        await session.commit()

asyncio.run(main())
PY
```

密码要求：含字母与数字、非弱口令；登录后立即在「设置」中修改。之后日常
用户/角色/邀请码管理全部走前端「设置」页或 admin API。

## 3. 前端两种构建路径

| | 路径 A：build-in-image（默认） | 路径 B：host-build（override） |
| --- | --- | --- |
| 前端产物 | `frontend/Dockerfile` 内 node:20 构建 dist 并烘焙进 nginx 镜像 | 宿主机 `cd frontend && npm ci && npm run build` 产出 `dist/` |
| 启动 | `scripts/deploy.sh` | `GD_COMPOSE_FILES="deploy/docker-compose.yml deploy/compose.host-build.yml" scripts/start.sh` |
| nginx 镜像 | `gold-desire-frontend:latest`（dist 烘焙） | 官方 `nginx:1.27-alpine` + 挂载 `frontend/dist` |
| 适用 | 标准部署、升级全自动 | 服务器不想装 node 镜像构建层；本地已出 dist 直接上 |
| 注意 | 宿主机 `frontend/node_modules` 存在时会污染构建上下文（先删或用路径 B） | 每次改前端都要重新 `npm run build` 并 `restart.sh env` |

两条路径的 nginx 配置同源（`deploy/nginx.conf` 挂载为
`/etc/nginx/conf.d/default.conf`）。切换路径只需增删 `GD_COMPOSE_FILES` 中的
override 文件后 `scripts/restart.sh env`。

## 4. `.env` 全量说明（根目录 `gold-desire/.env`，模板 `.env.example`）

### 4.1 基础设施（compose 插值）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `POSTGRES_USER` / `POSTGRES_DB` | gold / gold_desire | 数据库账号与库名 |
| `POSTGRES_PASSWORD` | change-me | 【必改】与 `DATABASE_URL` 中口令保持一致 |
| `NGINX_HTTP_PORT` / `NGINX_HTTPS_PORT` | 80 / 443 | 对外端口（冲突时改） |
| `TIMEZONE` | Asia/Shanghai | 业务统一时区 |
| `BACKUP_KEEP` | 14 | 备份保留份数 |

### 4.2 应用（注入 api/worker/migrate，`app/core/config.py:Settings` 为准）

| 组 | 变量 | 默认（容器口径） | 说明 |
| --- | --- | --- | --- |
| 应用 | `APP_ENV` | prod | dev/test/staging/prod |
| | `APP_NAME` / `APP_VERSION` / `APP_DEBUG` | gold-desire / 0.1.0 / false | 调试模式会暴露详情，生产关闭 |
| 服务 | `SERVER_HOST` / `SERVER_PORT` / `SERVER_WORKERS` | 0.0.0.0 / 8000 / 1 | api 容器内监听与进程数 |
| 数据库 | `DATABASE_URL` | postgresql+asyncpg://gold:***@postgres:5432/gold_desire | 主机名固定为服务名 postgres |
| | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` / `DB_ECHO` | 20 / 10 / 30 / false | asyncpg 连接池 |
| 分页 | `PAGE_SIZE_DEFAULT` / `PAGE_SIZE_MAX` / `UPSERT_CHUNK_SIZE` | 20 / 200 / 500 | 无界返回禁令与批写分片 |
| 缓存 | `CACHE_BACKEND` | redis | 生产必须 redis（限流/HITL 依赖） |
| | `REDIS_URL` | redis://redis:6379/0 | 主机名固定为服务名 redis |
| | `CACHE_DEFAULT_TTL` / `CACHE_MAX_ENTRIES` | 60 / 10000 | TTL 分级基准 / 进程 LRU 上限 |
| 认证 | `JWT_SECRET` | change-me | 【必改】`openssl rand -hex 32` |
| | `JWT_ALGORITHM` / `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | HS256 / 30 / 14 | token 有效期 |
| | `PASSWORD_HASH_ROUNDS` | 3 | Argon2 时间成本 |
| Agent | `AGENT_MODEL_BASE_URL` / `AGENT_MODEL_API_KEY` / `AGENT_MODEL_NAME` | openai / 空 / gpt-4o-mini | 【必改】OpenAI 兼容接口三件套 |
| | `AGENT_TIMEOUT_SECONDS` / `AGENT_MAX_TURNS` / `AGENT_MAX_TOOL_CALLS` | 60 / 12 / 30 | 调用预算 |
| 上游源 | `HITHINK_API_KEY` / `HITHINK_BASE_URL` | 空 / fuyao.aicubes.cn | 同花顺（密钥只影响采集侧） |
| | `XUANGUTONG_*` / `EASTMONEY_*` | 空 / example | 选股通 / 东财占位 |
| CORS | `CORS_ORIGINS` / `CORS_ALLOW_CREDENTIALS` | localhost:5273… / true | 同域部署保持默认即可 |
| 限流 | `RATE_LIMIT_ENABLED` / `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | true / 600 / 60 | 全局令牌桶 |
| | `LOGIN_RATE_LIMIT_REQUESTS` / `LOGIN_MAX_FAILURES` / `LOGIN_LOCKOUT_MINUTES` | 10 / 5 / 5 | 登录独立限流 + 锁定 |
| 采集 | `INGEST_WINDOW_AUCTION/INTRADAY/TAILPAN/POSTMARKET` | 09:25-09:40 等 | 窗口覆盖（可选） |
| | `INGEST_TICK_SECONDS` / `INGEST_CALENDAR_TTL_SECONDS` | 15 / 432000 | 调度 tick / 日历缓存 |
| 新鲜度 | `GD_MARKET_FRESHNESS_MINUTES` | 720 | 超窗接口返回 stale=true |

> 改 `.env` 后生效方式：`scripts/restart.sh env`（recreate 容器注入新环境，
> 不重建镜像）；改上游密钥/模型配置均属此类。

## 5. 日常运维

### 5.1 脚本一览（`scripts/`，可从任意 cwd 执行）

| 脚本 | 作用 |
| --- | --- |
| `deploy.sh` | 首次部署：.env 准备 → build → up → 等 /ready → 管理员提示 |
| `start.sh` / `stop.sh` / `restart.sh [svc...]` | 启停/重启；`restart.sh env` 应用 .env 变更 |
| `upgrade.sh [--no-pull]` | git pull → build → `run --rm api alembic upgrade head` → up -d → 等 /ready |
| `backup.sh` | pg_dump → `backups/db-<ts>.sql.gz`，按 `BACKUP_KEEP` 滚动清理 |
| `restore.sh <dump>` | 停 api/worker → 重建库 → 灌 dump → 重启（需输 yes 确认） |

### 5.2 日志

- 容器日志（JSON 文件驱动，自动轮转 50m×10）：`docker compose -p gold-desire logs -f api worker nginx`；
- 结构化日志含 `request_id` 串联（api 中间件注入），跨 nginx → api 可对齐排查；
- 建议宿主机 crontab 每日备份：`0 3 * * * cd /opt/gold-desire && scripts/backup.sh`。

### 5.3 升级

`scripts/upgrade.sh`（迁移在 api 容器启动命令中亦会幂等重放，双保险）。
失败回滚：`docker compose -p gold-desire run --rm api alembic downgrade -1`
+ 回退代码后 `upgrade.sh --no-pull`。数据卷不受重建影响。

### 5.4 HTTPS 启用

1. 证书放 `deploy/certs/{fullchain,privkey}.pem`（勿入库）；
2. `deploy/nginx.conf` 取消 443 server 块与 HSTS 注释，改 `server_name`；
3. `deploy/docker-compose.yml` 取消证书挂载注释；`restart.sh env`。

## 6. 扩容

### PostgreSQL

- 单机纵向：调大 `deploy.resources.limits`（默认 1536m）与 `DB_POOL_SIZE`；
  `shared_buffers` 可经 `postgres` 服务 `command` 追加；
- 数据卷 `gold-desire_pg_data` 迁移：`docker compose stop api worker && docker run --rm -v gold-desire_pg_data:/from -v <新盘>:/to alpine cp -a /from/. /to/`；
- 读写分离/托管库：改 `DATABASE_URL` 指向外部实例并移除 postgres 服务（分区表
  与 upsert 均为标准 PG 能力，无扩展依赖）。

### Redis

- 纵向：调 `deploy.resources.limits` 与 `--maxmemory`（当前 384mb、noeviction
  ——限流/HITL 令牌不允许淘汰；容量吃紧优先加内存而非改淘汰策略）；
- 外部 Redis：改 `REDIS_URL` 后移除 redis 服务；AOF 已开，重启不丢令牌。

### api

- `SERVER_WORKERS` 调大（或 api 服务 `deploy.replicas`，注意 pg 连接池 =
  workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW) 不超过 PG `max_connections`）。

## 7. 常见故障排查

| 症状 | 排查 | 处置 |
| --- | --- | --- |
| `/ready` 503 `database: ok=false` | `docker compose -p gold-desire logs postgres` | 密钥不一致 → 对齐 `POSTGRES_PASSWORD` 与 `DATABASE_URL` → `restart.sh env` |
| `/ready` 503 `cache: ok=false` | logs redis；`exec redis redis-cli ping` | 容器 OOM → 扩 limits/maxmemory |
| api 起不来：alembic 报错 | `logs api` 看迁移栈 | 版本冲突时 `alembic history` 对齐；无法修复则 `restore.sh` 回上一备份 |
| 前端 502 | api 是否 healthy；`nginx` 容器内 `wget -qO- api:8000/health` | api 未就绪则等迁移完成；持续 502 看 api 日志 |
| WS 连不上/频繁断 | 浏览器控制台关闭码；`4401`=token 失效 | 重新登录；检查 nginx `/ws` location 是否被改 |
| Agent 无回复/超时 | logs api 搜 `agent_stream_failed` | 检查 `AGENT_MODEL_*` 三件套；模型不可用会降级为明确错误提示 |
| 数据一直 stale | `GET /api/ingest/health`；logs worker | 上游密钥/窗口/主备顺序（`/api/datasources`）；worker 挂了 restart |
| 采集任务不跑 | 非交易日？窗口配置？`ingest_state` 标记？ | `INGEST_WINDOW_*` 核对；手动触发采集接口验证 |
| 登录一直 429/锁定 | Redis 限流键 | 等锁定期（默认 5min）；确认 `LOGIN_RATE_LIMIT_*` |
| 磁盘涨 | `docker system df`；`backups/`；PG 体积 | 清理旧备份调 `BACKUP_KEEP`；raw 30 天自动清，无需手工 |

## 8. 性能压测（Task 17 交付，此处为入口）

压测脚本位于 `scripts/bench/`（Task 17 交付：场景化 wrk/k6 脚本 + 结果汇总），
验收口径（spec）：**1 万并发读**下 P95 ≤ 200ms、错误率 < 0.1%、上游数据源零调用。
方法要点：先登录换 token → 对 `/api/sentiment` `/api/pools/limit_up` `/api/advice`
等读接口加压 → 观察容器资源与 PG 连接池 → 结果与 `/ready`、`stale` 语义对照。
