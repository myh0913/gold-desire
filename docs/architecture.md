# gold-desire 架构说明

> 阅读对象：需要理解/扩展本平台的开发者。部署运维见 [deploy-ops.md](deploy-ops.md)。

## 1. 分层总览

后端严格单向分层，**依赖方向只能自上而下**（api → services → repositories → db → core）：

```
app/
├── core/          # L0 配置(config) / 安全(security,captcha) / 缓存(cache) /
│                  #    日志(logging) / 中间件(middleware) / 限流(rate_limit) / 错误(errors)
├── db/            # L1 引擎与会话(session) / 模型基类(base) / 月分区(partitions)
├── models/        # L1 ORM 模型：auth / config / market / raw / derived
├── repositories/  # L2 唯一 DB 读写出口（业务代码禁止绕过）
├── services/      # L3 业务逻辑（auth/market/report/config/datasource/agent/…）
├── api/           # L4 路由层：参数校验(Pydantic) + 依赖注入，禁止直接触达 ORM/数据源
├── datasources/   # 数据源域：contracts(能力契约) / providers / mappings(声明式映射) / resolve
├── factors/       # 因子域：base(协议) / registry(注册+参数解析) / builtin / stats / cache
├── strategies/    # 策略域：protocol / context / registry / loader / plugins/
├── engine/        # 回测与撮合：backtest / portfolio / sell_rules / segments / dragon_legacy
├── ingest/        # 采集域：scheduler / tasks / pipeline / windows / replay / api_hooks
└── agent/         # Agent 域：client(OpenAI 兼容) / loop(SSE) / skills / tools / permissions
```

两条关键红线（有架构测试兜底 `test_no_direct_source_imports.py` 等）：

- **路由层不得直接访问 ORM 或数据源**——一切经 services → repositories / resolve。
- **业务代码不得 import 具体 provider**——只允许经能力契约与 `resolve_order` 取数。

## 2. 关键设计决策

### 2.1 能力契约 + 声明式映射（datasources/）

- 每个**能力**（capability，如 `daily_bars` / `limit_up_pool`）有一个 Pydantic 契约模型
  （`datasources/contracts/`），字段为**领域标准名 + 显式单位**（如 `volume_shares` 股、
  `amount_yuan` 元、`turnover_rate` 小数），不携带任何源命名。
- 每个 provider 只取回**原始 payload**（含源方信封），字段归一化全部交给
  `mappings/defs/<source>.py` 的声明式 `FieldMap(契约字段, 源字段路径, 换算函数)`。
- `resolve` 层只做 `model_validate(mapped)`：缺必需字段即拒绝该记录并写结构化告警
  （`data_degraded`），**绝不静默填 0**。
- `CAPABILITY_PROVIDERS` 维护能力 → 有序源列表（主源在前），运行时主备切换经
  `set_capability_order` 覆盖（由 `PUT /api/datasources/prefs` 持久化落库）。
- **效果**：换源/新增源 = 新增 provider + mapping + 调整优先级，消费方零改动。
  扩展方法见 [extend-datasource.md](extend-datasource.md)。

### 2.2 策略插件 + 门控下沉（strategies/）

- 策略是**目录式插件**（`strategies/plugins/<name>/`），声明 `strategy_id / label /
  version / phases / params_schema / gate_matrix`，生命周期钩子可选实现。
- 情绪周期门控矩阵由策略**自身声明**（`gate_matrix: 周期态 → GateRule(allowed,
  position_factor)`），核心只按协议评估——修复旧项目 `cycle.py::_GATE` 以中文策略名
  为 key、新策略忘改核心即被静默禁用的缺陷。
- 参数以 `strategy_id` 为命名空间存于 `contextvars.ContextVar`（并发任务互不可见），
  解析顺序：**运行时覆盖 > DB active 配置 > 代码默认**。
- 单策略异常不影响其他策略（结构化错误落 `advice_reports` 可查询）。
  扩展方法见 [extend-strategy.md](extend-strategy.md)。

### 2.3 采集/服务分离（ingest/ vs api/）

- **worker**（`python -m app.ingest.scheduler`）按窗口定时拉源 → 标准化 → 落库；
- **api** 只读 PostgreSQL + Redis，**用户请求路径零上游调用**——上游故障时读接口
  仍正常返回库中数据并带 `stale` 标记。
- 采集幂等：`(capability, trade_date)` 为幂等键，覆盖写；执行明细落 `ingest_jobs`。
- 保留策略由 worker 盘后维护：`raw_*` 30 天、`std_minute_bars` 90 天、日线与
  建议报告永久（`repositories/retention.py`）。

### 2.4 两级缓存（core/cache + services/cache_policy）

- **L1 Redis**：跨进程共享，TTL 分级（行情/情绪/配置各有档位），写后失效 + TTL 兜底；
- **L2 进程内 LRU**：热点短 TTL（`CACHE_MAX_ENTRIES` 封顶）。
- 生产 `CACHE_BACKEND=redis`（同时承载限流令牌桶与 Agent HITL 确认令牌）。

### 2.5 回放防未来函数（ingest/replay.py）

- 回测/沙箱给定历史日期 D 时，数据访问层只返回 `trade_date <= D` 的已入库数据；
- 所需数据缺失时抛 `SnapshotMissing` 并终止，**绝不回退实时源补齐**——保证回测可复现
  （同区间 + 同参数版本 = 同结果）。

### 2.6 因子注册表 + 配置驱动（factors/）

- 因子是纯函数（只依赖 `FactorContext`），阈值全部经 `FactorParamSpec` 声明为可配置
  参数；改阈值 = 写一条新的 `active` 配置版本，无需改代码/重启。
- 参数版本化（draft/active/archived）+ 有效性统计（按档位 × A/B/C 三段输出样本数/
  期望/胜率，`factors/stats.py`）。详见 [extend-factor.md](extend-factor.md)。

### 2.7 Agent 分级工具层（agent/）

- OpenAI 兼容接口接入模型（base_url/api_key/model 全可配），SSE 流式交互；
- 工具按**只读 / 变更**分级：变更工具仅 admin，清单过滤 + 执行前二次校验双层强制；
- 危险操作 HITL：`requires_confirmation` 的工具首轮不执行，凭绑定会话+用户的一次性
  令牌确认后才真正执行；全量审计（`agent_sessions/messages/tool_calls`）。
  详见 [extend-agent-tool.md](extend-agent-tool.md)。

## 3. 数据流图

```
                上游数据源                          模型服务（OpenAI 兼容）
        hithink / xuangutong / …                     base_url + api_key
              │ raw payload                                │ SSE
              ▼                                           ▼
   ┌─────────────────────┐  声明式映射+契约校验  ┌──────────────────┐
   │ providers (限频/重试) │ ─────────────────► │  resolve/registry │   ← 主备切换 set_capability_order
   └─────────────────────┘                     └──────────────────┘
              │                                          │ 契约对象
              ▼                                          ▼
   ┌─────────────────────┐   raw_*  ┌──────────────────────────────┐
   │ raw_responses 留档30d │ ◄────── │  ingest pipeline / scheduler │ worker 容器
   └─────────────────────┘          │  窗口: auction/intraday/      │
                                    │        tailpan/postmarket     │
                                    │  幂等: (capability, trade_date)│
                                    └──────────────┬───────────────┘
                                                   │ std_* / derived_*（月分区）
                                                   ▼
                                     ┌──────────────────────────────┐
        ┌───────────── 写路径 ────────►│       PostgreSQL 16          │◄─ alembic 迁移
        │  (策略/因子/用户/审计)        │  raw_* / std_* / derived_*   │
        │                             └──────────────┬───────────────┘
        │                                            │ 只读
   ┌────┴────────┐                        ┌──────────▼───────────┐     ┌──────────┐
   │ services 层  │ ──两级缓存────────►    │  Redis 7（L1 缓存/限流 │◄──► │ api 容器  │
   │ +repositories│ ◄─────────────────   │  /HITL 令牌，AOF）     │     │ uvicorn  │
   └─────────────┘                        └──────────────────────┘     └────┬─────┘
                                                                      /api │ /ws(WS)
                                                                           ▼
                                                              ┌────────────────────┐
                                                              │ nginx（静态 dist +   │
                                                              │  反代 /api /ws/SSE） │
                                                              └─────────┬──────────┘
                                                                        ▼
                                                    React SPA（TanStack Query + zustand + WS 客户端）
```

## 4. 进程与部署形态

| 进程 | 入口 | 职责 | 部署 |
| --- | --- | --- | --- |
| api | `uvicorn app.main:app` | 读 API / 认证 / Agent SSE / WS；启动时先 `alembic upgrade head` | 容器 `api`，仅暴露给 nginx |
| worker | `python -m app.ingest.scheduler` | 窗口采集 / 幂等状态 / 保留策略 / 分区维护 | 容器 `worker`，与 api 同镜像 |
| nginx | `deploy/nginx.conf` | SPA 托管 + 反代 + 安全响应头 + gzip | 容器 `nginx`，唯一对外入口 |

完整部署矩阵与运维见 [deploy-ops.md](deploy-ops.md)。
