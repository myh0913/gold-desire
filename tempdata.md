# 临时产物与数据清单

> 统计口径：**2026-09-18 08:00 ~ 2026-09-19 01:00**（扫描时刻）期间新建或被修改的文件/目录。
> 统计范围：`/private/tmp`、`project/quant-system/data`、`project/quant-web`、`project/logs`、Trae IDE job 目录。
> 时间戳均为本机时间（Asia/Shanghai）。

---

## 0. 汇总速览

| # | 目录 | 文件数 | 大小 | 性质 |
|---|---|---|---|---|
| 1 | `/private/tmp/dr_*`（龙回头分析脚本/中间数据） | 24 | 6.0M | 中间产物，**可删** |
| 2 | `quant-system/data/raw/hithink/2026-09-18/` | 967 | 25M | 原始行情，可重建 |
| 3 | `quant-system/data/raw/{eastmoney,xuangutong}/2026-09-18/` | 9 | 384K | 原始行情，可重建 |
| 4 | `quant-system/data/std/`（96 个日期目录） | 5443 | 89M | 标准快照，**7 天滚动清理** |
| 5 | `quant-system/data/advice/2026-09-18/` | 11 | 48K | 业务产出，**应保留** |
| 6 | `quant-system/data/{state,config}/`（本轮新增文件） | 6 | 12K | 运行时状态，**应保留** |
| 7 | `quant-web/dist/` + `backend/quant.db` + `lianban_ladder/` | — | 6.3M | 构建/业务产物 |
| 8 | `project/logs/` | 3 | 960K | 日志，**应保留** |
| 9 | Trae IDE `jobs/job-*` | 128 个目录 | 6.3M | IDE 日志，**可删** |
| 10 | 其他 `/private/tmp` 杂项 | 7 | 1.0M | 系统/IDE 临时，**可删** |

---

## 1. `/private/tmp` —— 龙回头分析中间产物（24 个文件，6.0M）

全部为本次"龙回头四路策略"分析链产生，业务代码不依赖，**可整体删除**。

### 1.1 分析脚本（.py，15 个）

| 文件 | 大小 | 时间 | 用途 |
|---|---|---|---|
| `dr_probe.py` | 2.4K | 09-18 23:13 | 探针：验证字段/接口可用性 |
| `dr_mine2.py` | 10K | 09-18 23:16 | 样本挖掘（375 条） |
| `dr_analyze.py` | 16K | 09-18 23:23 | 表 A~D：买点/单因子边际 |
| `dr_design.py` | 8.5K | 09-18 23:44 | 三路方案设计 |
| `dr_1445.py` | 6.3K | 09-18 23:45 | 14:45 口径派生字段 |
| `dr_final.py` | 4.9K | 09-18 23:45 | 最终三路定义 |
| `dr_v2.py` / `dr_v3.py` | 5.5K / 8.4K | 09-18 23:46 / 23:47 | 迭代版 |
| `dr_multi.py` / `dr_multi2.py` / `dr_multi3.py` | 6.8K / 6.2K / 5.7K | 09-18 23:58 ~ 09-19 00:00 | 表 E：卖出规则敏感性 + 多路合并 |
| `dr_robust.py` | 4.9K | 09-19 00:01 | 表 G：阈值邻域稳健性 |
| `dr_port.py` | 6.8K | 09-19 00:01 | 表 F：组合资金曲线 |
| `dr_detail.py` | 15K | 09-19 00:26 | 表 H：逐因子边际 + 贪心组合 |
| `dr_cmp.py` | 8.4K | 09-19 00:28 | 简版/详细版/混合版对比 |
| `dr_verify.py` | 9.0K | 09-19 00:28 | 表 I：加分项分层 + 组合模拟 |

### 1.2 中间数据（.json，2 个，5.9M）

| 文件 | 大小 | 时间 | 说明 |
|---|---|---|---|
| `dr_samples.py.json` | 3.6M | 09-18 23:18 | 375 条结构合格样本（文件名带 `.py` 系笔误） |
| `dr_min_cache.json` | 2.2M | 09-18 23:46 | 分时缓存 `{"<code>|<D日>": [["HH:MM", price, vol_lots], ...]}` |

### 1.3 分析输出（.txt，7 个，104K）

`dr_out.txt`(23K, 23:23) / `dr_design.txt`(11K, 23:44) / `dr_e.txt`(10K, 00:01) / `dr_g.txt`(8.7K, 00:01) / `dr_h.txt`(41K, 00:26) / `dr_i.txt`(8.9K, 00:28)

---

## 2. `quant-system/data/raw/` —— 原始数据

| 目录 | 文件数 | 大小 | 目录 mtime |
|---|---|---|---|
| `raw/hithink/2026-09-18/` | 967 | 25M | 09-19 00:00 |
| `raw/eastmoney/2026-09-18/` | 4 | 80K | 09-18 14:45 |
| `raw/xuangutong/2026-09-18/` | 5 | 304K | 09-18 17:00 |

- `raw/hithink/` 总计 125M；本轮新增的 `2026-09-18` 目录占 25M（含涨停池/日线/分时原始响应）。
- 相邻目录 `hithink/2026-09-17/`（631 文件 7.3M）、`eastmoney/2026-09-17/`、`xuangutong/2026-09-17/` 的 mtime 为 09-18 01:37，**早于 08:00 阈值**，不计入本轮。
- 性质：可由数据源重新拉取，属于**可重建数据**；如需清理按日期目录整体删除即可。

---

## 3. `quant-system/data/std/` —— 标准快照（总计 89M / 5443 文件 / 96 个日期目录）

按规范保留 7 天并每日自动清理，但**本轮分析触发了大批历史日期目录的重新落盘**，96 个日期目录的 mtime 全部落在 09-18 08:00 之后。

### 3.1 本轮新增/重写的重点目录

| 日期目录 | 文件数 | 大小 | mtime |
|---|---|---|---|
| `2026-09-18/` | 2969 | 54M | 09-18 23:18 |
| `2026-09-16/` | 403 | 4.4M | 09-18 23:46 |
| `2026-09-14/` | 270 | 3.1M | 09-18 23:46 |
| `2026-09-17/` | 227 | 2.4M | 09-18 23:18 |
| `2026-09-15/` | 38 | 416K | 09-18 23:46 |
| `2026-09-19/` | 1 | 4K | 09-19 00:00 |
| `2026-09-07/` | 21 | — | 09-18 23:18 |

`std/2026-09-19/` 仅 1 个文件：`trading_calendar-bf21a9e8fbc5.json`。
`std/2026-09-18/` 内容为 `daily_bars-*.json`、`auction_series-*.json` 等按内容哈希命名的快照文件（2969 个）。

### 3.2 回测窗口批量回填目录（mtime 统一为 09-18 23:46）

`2026-05-29 ~ 2026-09-11` 共约 78 个交易日目录，为本次 80 日回测窗口批量补全的快照。

### 3.3 探针样本目录（4 个）

| 日期目录 | 文件数 | 大小 |
|---|---|---|
| `2025-09-01/` | 1 | 16K |
| `2025-12-01/` | 2 | 40K |
| `2026-03-02/` | 2 | 48K |
| `2026-05-28/` | 1 | 36K |

多为零散探针（`trading_calendar` / `daily_bars`），**可删**。

---

## 4. `quant-system/data/advice/2026-09-18/` —— 业务产出（11 文件，48K）

**应保留**（每日策略报告落盘目录）：

| 文件 | 大小 | 时间 | 说明 |
|---|---|---|---|
| `auction_grab_092511.json` | 7.7K | 09:25 | 集合竞价抢筹 |
| `intraday_plan_092613.json` | 933B | 09:26 | 盘中计划（9:26 周期复核） |
| `intraday_002491_094225.json` | 535B | 09:42 | 盘中确认-002491 |
| `intraday_002846_094225.json` | 536B | 09:42 | 盘中确认-002846 |
| `intraday_603082_094225.json` | 535B | 09:42 | 盘中确认-603082 |
| `tailpan_pool_144516.json` | 4.0K | 14:45 | 尾盘选股池 |
| `cycle_170032.json` | 419B | 17:00 | 情绪周期 |
| `lianban_pool_170031.json` | 2.9K | 17:00 | 连板池 |
| `review_170033.json` | 2.9K | 17:00 | 当日复盘 |
| `dragon_pool_194032.json` | 3.5K | 19:40 | 龙回头建池 |

> 另有 `intraday_plan_013744.json`（09-18 01:37），早于 08:00 阈值。

---

## 5. `quant-system/data/{state,config}/` —— 运行时状态

### 5.1 `state/`（本轮 3 个）

| 文件 | 时间 | 说明 |
|---|---|---|
| `calendar.json` | 09-19 00:00 | 交易日历（每日 00:00 刷新） |
| `intraday-plan-2026-09-18.json` | 09-18 09:26 | 当日盘中计划快照 |
| `scheduler-2026-09-18.json` | 09-18 17:00 | 调度器当日执行记录 |

### 5.2 `config/`（本轮 3 个）

| 文件 | 时间 | 说明 |
|---|---|---|
| `datasources.json` | 09-18 19:36 | 数据源配置 |
| `schema.json` | 09-18 19:36 | 策略参数 schema（前端表单自动渲染用） |
| `perf.json` | 09-18 17:00 | 策略绩效统计 |

> `backtest/` 下 3 个回测任务目录（`20260917_173106/173333/173353`）时间为 09-18 01:31~01:33，**早于 08:00 阈值**。

---

## 6. `quant-web/` —— 前端构建与后端业务数据

| 路径 | 大小 | 时间 | 说明 |
|---|---|---|---|
| `quant-web/dist/`（`index.html` + `assets/`） | 1.2M | 09-18 19:37 | 前端构建产物，可重新 `npm run build` |
| `quant-web/backend/quant.db` | 1.2M | 09-18 19:36 | SQLite 业务库 |
| `quant-web/backend/data/lianban_ladder/2026-09-18.json` | — | 09-18 09:25 | 连板天梯当日数据 |
| `quant-web/backend/data/lianban_ladder/2026-09-17.json` | — | — | 前一日 |
| `lianban_ladder/` 目录合计 | 3.9M | — | 历史天梯数据 |

---

## 7. `project/logs/` —— 运行日志（**应保留**）

| 文件 | 大小 | 最后写入 |
|---|---|---|
| `backend.log` | 903K | 09-19 00:00 |
| `frontend.log` | 20K | 09-18 23:39 |
| `scheduler.log` | 3.2K | 09-19 00:00 |

> `build.log`（1.0K，09-15 21:39）早于阈值。

---

## 8. Trae IDE job 目录（128 个，6.3M）

路径：`/private/var/folders/3l/p5c9dffx6rzc7s02tcvhb2900000gn/T/trae-agent-toolhost-501/jobs/`

- 该目录共 516 个 `job-*`，其中 **128 个** 的 mtime 在 09-18 08:00 之后（本次会话的工具调用日志）。
- 另有 `.pool/`（09-19 00:56 更新）与 `.DS_Store`（57K）。
- 性质：IDE 运行日志，**可整体删除**（不影响任何业务数据）。

---

## 9. 其他 `/private/tmp` 杂项（**可删**）

| 路径 | 大小 | 时间 | 说明 |
|---|---|---|---|
| `mcp.log` | 354K | 09-19 00:41 | MCP 服务日志 |
| `funnel-verify.html` | 634B | 09-18 18:49 | 一次性验证页 |
| `openclaw/` | 680K | — | openclaw 运行时临时目录 |
| `openclaw-state-locks-501/` | 0B | — | 状态锁目录 |
| `cu-501/` | 0B | — | computer-use 临时目录 |
| `boost_interprocess/` | 0B | — | boost 进程间通信 |
| `powerlog/` | 0B | — | 系统电源日志 |

---

## 10. 本轮被修改的源码（非"产生文件"，但同属本轮改动）

| 文件 | mtime |
|---|---|
| `quant-system/src/quant_system/analyzer.py` | 09-18 18:29 |
| `quant-system/src/quant_system/strategy/tailpan.py` | 09-18 19:35 |
| `quant-system/src/quant_system/strategy/dragon.py` | 09-18 19:35 |
| `quant-system/tests/test_tailpan.py` | 09-18 19:36 |
| `quant-system/tests/test_dragon_shape.py` | 09-18 19:36 |
| `quant-system/tests/test_strategy_registry.py` | 09-18 19:13 |
| `quant-web/src/types/index.ts` | 09-18 19:30 |
| `quant-web/src/pages/今日建议.tsx` | 09-18 19:36 |
| `gold-desire/README.md`（龙回头详细版方案） | 09-19 00:41 |
| `quant-system/**/__pycache__/*.pyc` | 本轮生成，**可删** |

---

## 11. 清理建议

**可安全删除（无业务影响）**

```bash
# 1) 本轮龙回头分析链（脚本 + 样本 + 分时缓存 + 输出）
rm -f /private/tmp/dr_*.py /private/tmp/dr_*.json /private/tmp/dr_*.txt
# 2) IDE 日志
rm -rf /private/var/folders/3l/p5c9dffx6rzc7s02tcvhb2900000gn/T/trae-agent-toolhost-501/jobs/job-*
# 3) 零散探针快照
rm -rf quant-system/data/std/2025-09-01 quant-system/data/std/2025-12-01 \
       quant-system/data/std/2026-03-02 quant-system/data/std/2026-05-28
# 4) 字节码缓存
find quant-system -name __pycache__ -type d -exec rm -rf {} +
```

**需保留**

- `quant-system/data/advice/2026-09-18/`（当日全部策略报告）
- `quant-system/data/state/`、`quant-system/data/config/`（运行时状态与配置）
- `quant-system/data/std/2026-09-14 ~ 2026-09-19`（7 天滚动窗口内）
- `project/logs/`（排查问题用）
- `quant-web/backend/quant.db`、`quant-web/backend/data/lianban_ladder/`

**需谨慎**

- `quant-system/data/std/` 中 2026-05-29 ~ 2026-09-11 的历史快照：虽超出 7 天保留期，但**是 80 日回测的唯一零 API 数据源**，删除后回测需重新拉取。
- `quant-web/dist/`：删了需重新构建前端。
