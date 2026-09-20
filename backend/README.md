# gold-desire 量化平台后端

全栈量化交易平台的后端服务，基于 FastAPI + SQLAlchemy 2.0 async 构建。

## 分层架构

```
core（配置/日志/缓存/异常/中间件）→ db（引擎/会话/模型）→ repositories（唯一 DB 出口）
→ services（业务）→ api（路由）
```

- 路由层 SHALL NOT 直接访问 ORM 或上游数据源。
- 数据源、因子、策略均采用可插拔 / 注册表 / 插件目录式设计。
- 采集（写库）与服务（只读库 + Redis）分离，请求路径零上游调用。

## 快速开始（无需 PostgreSQL / Redis）

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/cp .env.example .env   # 按需调整
./.venv/bin/uvicorn app.main:app --reload
```

- 开发默认使用 SQLite + 进程内 memory 缓存，可直接启动。
- 生产切换：`DATABASE_URL=postgresql+asyncpg://...` + `CACHE_BACKEND=redis` + `REDIS_URL=...`。

## 质量工具

```bash
./.venv/bin/ruff check app tests
./.venv/bin/ruff format --check app tests
./.venv/bin/mypy app
./.venv/bin/python -m pytest tests/
```

## 探针

- `GET /health`：存活（恒 200）
- `GET /ready`：就绪（检查 db / 缓存连通性，异常返回 503）