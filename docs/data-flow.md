# 数据流说明（上游 → 库 → API → 前端）

> 配套阅读：[architecture.md](architecture.md)（分层与设计决策）、
> [extend-datasource.md](extend-datasource.md)（换源/新增源）。

## 1. 端到端数据流

```
上游 API ──(provider: 限频令牌桶 + 指数退避重试)──► raw payload
   │ 1. 原始响应留档 raw_responses（30 天，排障用）
   │ 2. 声明式映射 FieldMap：源字段 → 契约字段（单位换算/类型转换）
   │ 3. resolve：model_validate 契约校验，缺字段拒绝 + data_degraded 告警
   ▼
std_* / derived_* 分区表（月分区，幂等覆盖写 upsert）
   │                                    ┌────────────────────────┐
   │──── ingest_jobs 明细 ─────────────►│ job/capability/source/  │
   │                                    │ 状态/行数/耗时/重试次数 │
   ▼                                    └────────────────────────┘
读 API（api 容器）── 两级缓存（Redis → 进程 LRU）──► nginx ──► 前端
                                    └── WS 推送（建议/采集/情绪/告警）
```

**不变量：用户请求只命中 PostgreSQL / Redis，绝不触达上游。** 上游全挂时读接口
照常返回库中已有数据，并以 `stale` 标记新鲜度。

## 2. 采集侧（worker：`python -m app.ingest.scheduler`）

### 2.1 执行窗口（`app/ingest/windows.py`，可用环境变量覆盖）

| 窗口 | 默认区间 | 内容 |
| --- | --- | --- |
| auction | 09:25–09:40 | 竞价池（涨停池首封） |
| intraday | 09:26–10:00 | 盘中轮询（快讯等，按 interval 重复） |
| tailpan | 14:45–15:00 | 尾盘题材（题材榜 / 题材个股） |
| postmarket | 17:00–18:00 | 盘后日线 / 天梯 / 情绪 / 交易日历 |

覆盖方式（`.env`）：`INGEST_WINDOW_AUCTION=09:25-09:40`、
`INGEST_TICK_SECONDS=15`（调度 tick）、`INGEST_CALENDAR_TTL_SECONDS=432000`
（日历缓存 5 天）。交易日历取自 `trading_calendar` 能力并缓存（复用
`pool_snapshot`），上游失败回退「周一至五」并告警，不崩溃。

### 2.2 幂等与状态

- 每能力注册一个 `IngestTaskDef`（capability / target 写入器 / window /
  interval / args_builder），见 `app/ingest/tasks.py`。
- `args_builder` 返回**一组**取数参数，每个任务可多轮取数（逐份取数、逐份留档、
  行数累加）；参数为空表示「无标的可采」，按成功 0 行处理。典型例子是
  `daily_bars`：上游按单只证券取历史区间，故其标的（**近期涨停池 + 连板天梯推导出的
  主板非 ST 票**）先由 `args_builder` 算出并补写 `stocks`，再逐票取数。
- 幂等键 `(capability, args, trade_date)`：重复执行**覆盖写**而非重复插入
  （各表按 `(code, trade_date)` / `(trade_date, pool_type, code)` 等唯一键 upsert，
  批量按 `UPSERT_CHUNK_SIZE` 分批）。
- 当日已完成任务落 `pool_snapshot(pool_name='ingest_state:<task>')`——重启不重跑；
  **失败不标记完成**，窗口内按退避 + jitter 重试（尊重上游 `Retry-After`）。
- 手动触发与健康度：admin 接口 `POST /api/ingest/...` 与
  `GET /api/ingest/health`；Agent 工具 `get_ingest_health`。

### 2.3 保留策略（`app/repositories/retention.py`，盘后维护任务执行）

| 数据 | 保留 |
| --- | --- |
| `raw_*` 原始响应 | 30 天 |
| `std_minute_bars` 分钟线 | 90 天 |
| `std_daily_bars` 日线 | 永久 |
| `advice_reports` 建议报告 | 永久 |

同一维护任务还负责**预建未来月分区**（`db/partitions.py`），避免月初写入失败。

### 2.4 回放模式（防未来函数）

回测/沙箱给定历史日期 D：数据访问层只返回 `trade_date <= D` 的已入库数据；
缺失即抛 `SnapshotMissing` 终止，**不回退实时源**。同区间 + 同参数版本重跑
结果完全一致（幂等），并按 A/B/C 三段分别输出。

## 3. 服务侧（api：读路径）

1. nginx（`deploy/nginx.conf`）反代 `/api/` → `api:8000`（后端路由自带 `/api`
   前缀，无 rewrite）；`/api/agent/**` 关闭代理缓冲（SSE）；`/ws` 带 Upgrade 头。
2. 路由层 Pydantic 校验 → services → repositories（参数化 ORM）→ 命中
   Redis（L1，TTL 分级）则直接返回；未命中查库回填。
3. 分页强制：列表接口默认 `PAGE_SIZE_DEFAULT=20`、上限 `PAGE_SIZE_MAX=200`，
   禁止无界返回。
4. 每次读路径的 DB 访问经 asyncpg 连接池（`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`）。

### stale 语义（`app/services/freshness.py`）

- 以「库中最近 `ingested_at`」与期望新鲜度窗口比较（默认 720 分钟 = 12h，可用
  `GD_MARKET_FRESHNESS_MINUTES` 覆盖）；
- 无任何入库记录或所需日期缺失 ⇒ `stale=true`；
- 判定只读库，不触发上游、不扫文件；前端据 `stale` + `data_date` 展示数据时间提示。

## 4. WebSocket 通道（`app/api/ws.py`）

- 连接：`/ws?token=<jwt access token>`（WS 无法自定义请求头）；无效/停用
  用户拒绝握手（关闭码 4401）。
- 协议：服务端 30s `heartbeat`；客户端 `ping` / `subscribe` / `unsubscribe`；
  推送 `{"type":<channel>,"channel":<channel>,"data":{...},"ts":<ms>}`。
- 频道：`sentiment`（情绪）、`pool`（涨停池/天梯）、`advice`（建议产出）、
  `newsflash`（快讯）、`themes`（主题）、`alert`（**仅 admin** 运维告警）。
- **发布者**：
  - `alert`：手动采集完成/失败通知（`ingest_service`）；
  - 行情类频道（`sentiment`/`pool`/`newsflash`/`themes`）：**调度器每个采集任务
    成功后**经 `ingest/events.py` 广播**薄事件**（只含 source/trade_date/rows，
    不带全量 payload），并按能力映射失效对应读缓存前缀（写后失效跨进程生效，
    无 Redis 时由 api 侧 L1 短 TTL 兜底）；
  - `pool` / `advice` 另由盘后策略阶段钩子（`ingest/strategy_hooks.py`）发布。
- **跨进程推送**经 Redis pub/sub 总线（`core/ws_bus.py`）：worker 进程发布 →
  api 进程订阅中转到本进程连接；无 Redis（单进程/内存缓存）时自动退化为
  进程内直发。
- 前端消费模型：**事件失效重取**（与旧 quant 的 30s 全量推送相反）——页面经
  `hooks/useChannelRefresh` 订阅频道，收到薄事件即失效对应查询键，由 REST
  按需重取（复用请求层缓存与鉴权，带宽与数据量无关）。
- 前端客户端封装重连退避 + 心跳 + 半开检测，断线重连后按需经 REST 补数据。

## 5. 前端消费（frontend/）

- **TanStack Query**：REST 缓存/重试/失效（`lib/queries/*` 按域组织）；
- **zustand**：UI 状态（抽屉开合、主题等）；
- **WS 客户端**（`lib/ws.ts`）独立封装，订阅变化驱动对应 query 失效重取；
- Agent 抽屉经 `lib/agentStream.ts` 消费 SSE，逐事件渲染工具调用过程。
- 时区统一 Asia/Shanghai（`lib/time.ts`），金额/百分比格式化统一 `lib/format.ts`。
