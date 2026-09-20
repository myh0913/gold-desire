# 扩展指南：新增数据源

> 目标：**只改一个模块**——新增 provider 文件 + 一份 mapping，并在能力顺序里登记，
> 策略/服务/API 代码零改动（有架构测试 `test_no_direct_source_imports.py` 守护）。

## 0. 三件套与职责边界

| 件 | 位置 | 职责 | 禁止 |
| --- | --- | --- | --- |
| 契约 | `app/datasources/contracts/models.py` | 能力的 Pydantic 模型（领域标准名+单位） | 出现任何源方字段名 |
| provider | `app/datasources/providers/<source>.py` | 取回**原始 payload**、认证、限频声明 | 做字段归一化/业务判断 |
| mapping | `app/datasources/mappings/defs/<source>.py`（或 YAML） | 源字段 → 契约字段的声明式映射 | 写条件分支逻辑 |

`resolve` 层只做 `model_validate(mapped)`，不含任何源的业务分支。

## 1. 步骤

### Step 1：确认/新增能力契约

若目标能力已存在（`daily_bars` / `limit_up_pool` / `ladder` / `market_sentiment` /
`theme_rank` / `theme_stocks` / `newsflash` / `trading_calendar` / `minute_bars` …），
直接复用；确需新能力时在 `contracts/` 增加模型并登记 `CAPABILITY_CONTRACTS`
（新能力同时需要一个 ingest 任务与写入器，见 Step 5）。

### Step 2：写 provider（模板见 `providers/hithink.py`）

```python
@register_provider
class MyProvider(BaseProvider):
    source_id = "mysource"                # 唯一源 id
    label = "我的行情源"
    kind = SourceKind.HTTP
    capabilities = ("limit_up_pool", "daily_bars")  # 必须已有契约，否则注册即报错
    rate_limit_per_min = 60               # 限频预算：采集侧按源分别限流
    priority = 20

    async def fetch(self, capability: str, args: dict[str, Any]) -> dict[str, Any]:
        # 只取回原始 payload（含源方信封），单位/字段名一律交给 mapping
        ...
```

要点：

- 重试复用框架 `request_with_retry`（指数退避 + jitter，尊重 `Retry-After`），
  不手写重试；
- 认证密钥走 `Settings`（`app/core/config.py`）+ 环境变量，不落代码；
- 业务错误码抛 `UpstreamError(detail=...)`，不吞错。

### Step 3：写 mapping（`mappings/defs/mysource.py`）

```python
_LIMIT_UP = CapabilityMapping(
    source_id="mysource",
    capability="limit_up_pool",
    record_path="data.list",                    # 记录数组在 payload 中的路径
    static={"pool_type": "limit_up"},           # 静态注入字段
    fields=(
        FieldMap("code", "stock_code", "normalize_code"),
        FieldMap("continue_days", "lb_count", "to_int"),
        FieldMap("seal_amount_yuan", "seal_money", "yuan_from_wan"),   # 万元→元
        FieldMap("turnover_rate", "turnover", "pct_to_ratio"),          # %→小数
        FieldMap("limit_up_time", "seal_time", "hhmm_from_str", required=False),
    ),
)
register_mapping(_LIMIT_UP)   # 或在模块 import 时注册（见 defs/__init__.py 惯例）
```

- 换算函数用 `mappings/transforms.py` 现有的（`pct_to_ratio` / `yuan_from_wan` /
  `lots_to_shares` / `ms_to_date` / `normalize_code` …），不够再补纯函数；
- **契约必需字段一律 REQUIRED**：缺失即拒绝该记录并写结构化告警
  （源/能力/缺失字段/原始响应指针），绝不填 0 或空串；
- 也可用 YAML 声明（见 `mappings/defs/example.yaml`），DSL 相同，适合无逻辑的纯映射。

### Step 4：登记能力顺序（主备）

`app/datasources/registry.py` 的 `CAPABILITY_PROVIDERS` 给出默认顺序（主源在前）：

```python
CAPABILITY_PROVIDERS["limit_up_pool"] = ["hithink", "mysource"]  # hithink 主源
```

启动断言 `assert_registry_consistent()` 会检查：能力有契约、源已注册、
(源, 能力) 有映射——漏任何一步启动即失败（fail-fast）。

**运行时主备调整入口**（Task 6 已交付，持久化落库）：
`PUT /api/datasources/prefs` 保存 `能力 → 有序源列表`，内部调
`set_capability_order()` 覆盖运行时顺序，**下一轮采集热生效**，无需重启；
配套 `POST /api/datasources/{source_id}/enable|disable` 启停源、
`POST /api/datasources/ping` 连通性探测（结果写健康度表）。

### Step 5：（仅新能力）注册采集任务

在 `app/ingest/tasks.py` 增加 `IngestTaskDef`：声明 capability、目标写入器
（`WRITERS`，契约 → 表列映射只在此处声明一次）、窗口、interval、args_builder
（返回**一组**取数参数，支持一任务多轮取数；返回空序列即「无标的可采」）。
已有能力换源**不需要**这一步。

## 2. 两个现成示例

### 2.1 fake provider（离线/测试）

`providers/fake.py`：所有能力的内置假源，是 `CAPABILITY_PROVIDERS` 的**默认值**
——`CAPABILITY_PROVIDERS` 初始全部指向 `fake`，真实源在接入并调整顺序后才生效。
**离线测试回落 fake 的行为**：测试与无密钥环境不配置真实源即自动用 fake，
保证策略/服务层测试可在零网络下全绿。

### 2.2 hithink 真实示例（摘录）

provider（`providers/hithink.py`）——只取原始响应：

```python
@register_provider
class HithinkProvider(BaseProvider):
    source_id = "hithink"
    capabilities = ("daily_bars", "limit_up_pool", "ladder", "trading_calendar")
    rate_limit_per_min = 120
    priority = 10
    # fetch: GET {base}/api/a-share/prices/historical 等端点，
    # 认证头 X-api-key（settings.hithink_api_key），
    # code=0 成功 / 2001、2003 上抛 UpstreamError
```

mapping（`mappings/defs/hithink.py`）——全部归一化在此：

```python
_DAILY_BAR = CapabilityMapping(
    source_id="hithink", capability="daily_bars", record_path="data.item",
    fields=(
        FieldMap("code", "thscode", "normalize_code"),       # 600519.SH → 标准码
        FieldMap("trade_date", "date_ms", "ms_to_date"),     # 毫秒 → date
        FieldMap("volume_shares", "volume", "lots_to_shares"),  # 手 → 股
        FieldMap("amount_yuan", "turnover", "yuan_from_wan"),   # 万元 → 元
        ...
    ),
)
```

## 3. 验证清单

1. `pytest tests/test_datasources.py tests/test_datasource_mappings.py`（fixture
   在 `tests/fixtures/<source>/`，用 `httpx.MockTransport` 离线走真实 fetch 路径）；
2. 新增 fixture：把上游真实响应样例放进 `tests/fixtures/mysource/`；
3. 启动时观察注册表断言不报错；`GET /api/datasources` 应列出新源；
4. 上线前 `PUT /api/datasources/prefs` 把新源加入对应能力顺序（可作备源灰度）。
