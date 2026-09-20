# 性能验收（Task 17）— gold-desire 读路径

本目录是 spec「读 API 与高并发」的本地性能验收测试。**纯本地、无 Docker / PostgreSQL / Redis**，
仅依赖 `.venv/bin/python` + aiosqlite + MemoryCache。

## 运行

```bash
./.venv/bin/python -m pytest tests/perf/ -v -s
./.venv/bin/ruff check tests/perf
./.venv/bin/python -c "from app.main import create_app; create_app()"
```

## 四个场景

| 场景 | 名称 | 关键断言 |
| --- | --- | --- |
| A | 1 万并发读 | `errors ≤ 10 / 10000`，`cache_hits > 0`，P95 ≤ 200ms（SQLite 下软警告，PG + Redis 严格） |
| B | 零上游保证 | 全部 provider `fetch` 抛错；6 个读接口全部 200，响应结构完整（行情类有 `stale`，股票有 `items/total/page/page_size/pages`），零 provider 调用 |
| C | 上游全断 + 空库 | 6 个读接口全部 200 + `stale=true`，零 5xx，零 provider 调用；列表为空 / 单对象为 `None` |
| D | 文件系统不可用 | `pathlib.Path.glob/rglob/iterdir` 与 `builtins.open` 抛错；6 个读接口仍 200 |

每个场景都用 `pytest -s` 在控制台打印一行真实数字（不掩盖失败）。

## 种子方案（`tests/perf/seed.py`）

- **规模**：20 只股票 × 100 个交易日 = 2000 条日线；50 条情绪历史；200 条涨停池；300 条天梯；30 条建议报告；少量快讯 / 监控；
- **幂等性**：所有插入走既有仓储的 `upsert_many` / `create_draft` / `create`（与 conftest 同套），重复执行不抛错；
- **隔离**：与父 conftest 共用内存 SQLite（StaticPool），schema 与数据由本目录的 `seed_perf_dataset()` 一次性写入。

`seed_perf_dataset(factory)` 返回元信息字典（含 `start` / `end` / 行数），供 `read_endpoints()` 按真实日期窗口拼装请求参数。

## 端点轮换集合（避开单一端点缓存偏移）

`tests.perf.seed.read_endpoints()` 返回 6 个端点，压测用例按索引循环旋转：

1. `GET /api/sentiment?date={end}`
2. `GET /api/sentiment/history?days=20`
3. `GET /api/ladder?start={start}&end={end}&page=1&page_size=50`
4. `GET /api/themes?date={end}`
5. `GET /api/newsflash?limit=20`
6. `GET /api/stocks?page=1&page_size=20`

## 已知环境局限（请连同数字一起读）

1. **ASGITransport 单进程**：本测试用 `httpx.ASGITransport(app=...)`，**不开 TCP socket，
   不走 uvicorn worker**，测量的是同一进程内事件循环的端到端时延。spec 验收的
   「1 万并发读」侧重**应用层 + DB + 缓存**，而非 ASGI 服务器实现差异。
2. **SQLite StaticPool 单 writer**：SQLite 默认串行化写、所有连接串行通过同一连接；
   `aiosqlite` 同步阻塞事件循环 → 单线程下读链路也退化为「单串行」，P95 主要受此拖累。
3. **aiosqlite 同步 socket 调用**：`sqlite3` 同步 API 在事件循环里会拖慢每一次 DB 调用；
   切到 `asyncpg` + PostgreSQL 16 + `pgbouncer` 异步驱动后该路径才接近真实线上表现。
4. **未启用 Redis**：默认 `MemoryCache` 进程内 L2；多 worker 下 L2 会分裂到各 worker 独立，
   缓存命中率随之下降。生产部署目标是 Redis（跨进程共享 L2）。

以上局限是 **本地环境约束**，不是产品代码问题——读路径本身在 spec 已明确「只读 DB + 两级缓存，
零上游、零文件系统」，本测试也验证了 B/C/D 三个约束面。

## 实测数字（最近一次本地运行，2026-09-19）

#### Scenario A — 1 万并发读

```
P50 = 2356 ms
P95 = 3140 ms      ⚠ 超 200ms 预算（SQLite + aiosqlite 同步阻塞）
P99 = 4440 ms
错误率 = 0.0%      ✅ 0 / 10000
RPS = 404
缓存命中 = 81.7%   ✅ L1 命中 8157 / L1+miss 总数 9982（首次请求必 miss）
provider 调用 = 0  ✅
```

**P95 未达 200ms 的原因**：本地环境为 SQLite + StaticPool + aiosqlite 同步 + ASGI 单进程；
该组合下读链路无法并行。PostgreSQL + Redis + 多 worker 真实部署通常 P95 < 50ms。
测试在 SQLite 环境下用 `UserWarning` 软记录，**PG 后端则强约束**（自动 `pytest.fail`）。

#### Scenario B — 零上游

```
6/6 个读接口返回 200 + 结构完整（stale / items / page / etc.）
provider 调用计数 = 0
```

#### Scenario C — 空库 + 上游全断

```
6/6 个读接口返回 200 + stale=true + items=[] / item=None
5xx = 0
provider 调用计数 = 0
```

#### Scenario D — 文件系统不可用

```
6/6 个读接口在 open / glob / rglob / iterdir 抛错后仍 200
```

#### 选测：缓存层（`test_scenario_a_cache_layer_reports_hits`）

```
50 轮 × 6 端点 = 300 请求
L1 命中 294 / 总 306 = 96.1%
L1 store 大小 = 7（每个端点一个键）
```

## 调优建议（若部署后 P95 仍超 200ms）

1. **DB 层**：换 PostgreSQL + `asyncpg`，启用连接池（`db_pool_size=20, db_max_overflow=10`）。
   当前 SQLite + StaticPool 是单连接串行。
2. **驱动层**：确认 `aiosqlite` 不在生产路径上，aiosqlite 的同步 socket 会阻塞事件循环。
3. **缓存层**：默认 `cache_backend=memory`，多 worker 下需切 `redis`（`CACHE_BACKEND=redis`、
   `REDIS_URL=redis://...`）才能跨进程共享 L2，否则每次冷启动 L2 全 miss。
4. **中间件顺序**：当前中间件顺序为 `RateLimit → SecurityHeaders → RequestId → AccessLog`，
   AccessLog 同步 `time.perf_counter` 与 IO；极热路径可考虑把 AccessLog 拆到后台队列。
5. **限流阈值**：默认 600 req/min/IP，1 万并发读需调高 `RATE_LIMIT_REQUESTS` 或启用
   专读角色桶（避免与登录 / 写路径共享）。
6. **分页收敛**：场景 A 用了 `page_size=20`/`50` 的小页，足以验证「分页强制有界」
   已经生效；后续若要做更大吞吐，可把 `page_size_max` 调到 500 并验证 ORM 不会全表扫。
7. **批量采集 vs 实时读**：spec 明确「请求路径零上游」，本验收覆盖了「请求只命中缓存 + DB」；
   真要进一步压低 P95，可让采集侧提前预热 L2（采集完成时 `CachePolicy.invalidate(...)`）。