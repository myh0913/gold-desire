# 扩展指南：新增策略

> 目标：**核心零改动**——新增一个目录即被自动发现；门控由策略自身声明，
> 调度器按 `phases` 自动编排。参考实现：`app/strategies/examples/echo_strategy.py`
> （最小模板）与 `app/strategies/plugins/dragon/`（龙回头，生产级）。

## 1. 目录结构

```
app/strategies/plugins/<my_strategy>/
├── __init__.py      # import 触发注册（loader 只扫 plugins/ 下的包）
├── strategy.py      # 策略主类（继承 BaseStrategy）
└── ...              # 按需拆分：gates.py / paths.py / bonus.py 等（见 dragon/）
```

> `examples/` 下的策略**不参与生产发现**（发现只扫 `plugins/`）；
> 想上线把文件移入 `plugins/` 即可。

`strategies/loader.py` 启动时自动扫描注册；**重复 `strategy_id` 启动即报错**。

## 2. BaseStrategy 协议（`app/strategies/protocol.py`）

```python
class MyStrategy(BaseStrategy):
    strategy_id = "my_strategy"          # 必填，全局唯一
    label = "我的策略"
    version = "0.1.0"
    description = "……"
    phases = frozenset({Phase.POOL, Phase.AUCTION})   # 参与的阶段，未声明不被调度
    params_schema = (                    # 可配置参数（与因子同形，前端自动渲染表单）
        StrategyParamSpec(key="threshold", label="阈值", type="float",
                          default=0.08, min=0.0, max=0.5, step=0.01,
                          unit="小数", description="…"),
    )
    gate_matrix = {                      # 情绪周期门控：策略自己声明，核心零改动
        CycleState.ICE:     GateRule(allowed=False, position_factor=0.0),
        CycleState.TURN:    GateRule(allowed=True,  position_factor=0.3),
        CycleState.REPAIR:  GateRule(allowed=True,  position_factor=0.5),
        CycleState.ACCEL:   GateRule(allowed=True,  position_factor=1.0),
        CycleState.DIVERGE: GateRule(allowed=True,  position_factor=0.5),
        CycleState.RETREAT: GateRule(allowed=False, position_factor=0.0),
    }

    async def build_pool(self, ctx: StrategyContext) -> Any: ...   # 生命周期钩子全部可选
```

### 2.1 phases 与钩子一一对应

| Phase | 钩子 | 时机 |
| --- | --- | --- |
| AUCTION | `run_auction_pipeline` | 9:25 竞价 |
| POOL | `build_pool` | 盘后建池 |
| SCENE | `classify_scenes` | 开盘场景分类 |
| INTRADAY | `confirm_intraday` / `intraday_expired` | 盘中确认 / 观察窗过期 |
| TAILPAN | `run_tailpan` | 尾盘处理 |

### 2.2 gate_matrix（门控下沉）

- 核心评估入口 `strategies/registry.evaluate_gate` 只读策略声明的矩阵；
- 周期态 `CycleState`：冰点 / 冰点转折 / 修复 / 加速·高潮 / 分歧 / 退潮（+ 未知）；
- 未声明的周期态默认 **放行但仓位系数 0.5**（`DEFAULT_GATE_POSITION_FACTOR`），
  不会像旧项目那样被静默禁用；`UNKNOWN` 态按矩阵缺省回退并在 reason 中说明。

### 2.3 参数：隔离与解析顺序

- 命名空间为 `strategy_id`：A、B 策略同名参数 `threshold` 互不影响；
- 解析顺序 **运行时覆盖 > DB active 配置 > 代码默认**；非法值回退默认并写
  `strategy_param_invalid` 结构化告警；
- 运行时覆盖存 `contextvars`（并发任务隔离，修复旧项目全局 dict 串台缺陷）。

### 2.4 依赖注入（StrategyContext）

一切依赖（配置、仓储句柄、时钟、日志、数据访问）经 `ctx` 注入；
**禁止** import 全局单例或数据源。取参数用 `await self.get_params(ctx)`，
取单参数用 `self.param(params, key)`（未声明的 key 直接 KeyError，防拼写错误）。

## 3. 复用公共资产

- **卖出规则**：`app/engine/sell_rules.py`（3% 止损 / 1 个可卖日等撮合口径）；
- **组合风控**：`app/engine/portfolio.py`（单票去重、同日总仓位 ≤ 80% 等比压缩）；
- **分段**：`app/engine/segments.py`（A/B/C 三段切分）；
- **加分项/门槛**：`plugins/dragon/bonus.py` / `gates.py` / `paths.py`（S2/S4 两路）；
- 因子经 `factor_id + 参数版本` 引用（`app/factors/registry.py`），
  **不要在策略代码里硬编码阈值常量**。

建议输出结构化建议记录（路次、命中硬门槛明细、加分项清单、建议仓位、止损价、
卖出时点、依据字段快照），落 `advice_reports`——前端提示卡片与 Agent 均消费该结构。

## 4. 龙回头参考（第一个生产插件）

`app/strategies/plugins/dragon/`：

- 两路：`paths.py` 的 `S2 = D+1 开盘买入`、`S4 = D+2 开盘买入`；
- 硬门槛（`gates.py`）：S2 = 尾盘跳水 ∧ 首阴振幅 ≥ 8% ∧ D+1 开盘 ≤ -3%；
  S4 = D+1 量/首阴量 < 0.6 ∧ 首阴振幅 ≥ 8%；
- 卖出两路一致：不设止盈 + 3% 止损 + 持 1 个可卖日（仅可卖日生效，逐分钟判定）；
- S4 按加分项加码（权重 ×(1 + 0.25×加分项数)，单路上限 1.5 倍），S2 不加码；
- 策略基线全文见仓库根 `readme.md`。

## 5. 启停与升级

- 启用/停用：`strategy_defs.enabled`（admin 经 REST 或 Agent 变更工具）；
- 定义同步：启动时把 `schema_dict()`（含 gate_matrix / params_schema）upsert 进
  `strategy_defs`，前端「量化配置」页自动出现新策略的参数表单。

## 6. 验证清单

1. 复制 `examples/echo_strategy.py` 为骨架，跑 `pytest tests/test_strategies.py`；
2. 断言：重复 id 报错 / 未声明周期态回退 0.5 / 同名参数互不影响；
3. 单策略抛异常不影响其他策略执行（失败明细落 `advice_reports`，前端与 Agent 可查）；
4. 回测复现：同区间 + 同参数版本重跑结果一致，A/B/C 三段分别输出。
