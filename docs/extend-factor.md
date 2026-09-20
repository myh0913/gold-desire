# 扩展指南：新增因子

> 目标：**改阈值不改代码**——因子是注册表里的纯函数，阈值全部参数化并版本化存库。

## 1. 因子协议（`app/factors/base.py`）

每个因子声明 `factor_id` / 显示名 / 类别 / 计算函数 / 参数 schema /
档位（Bucket）。计算只依赖 `FactorContext`（已装配好的行情与派生指标），
**禁止** import 仓储或数据源——保证可离线单测与回测可复现。

```python
# app/factors/builtin/amplitude.py（既有内置因子之一）
@dataclass(frozen=True, slots=True)
class AmplitudeFactor(BaseFactor):
    factor_id: str = "amplitude"
    label: str = "首阴振幅"
    category: str = "形态"
    params_schema: tuple[FactorParamSpec, ...] = (
        FactorParamSpec(key="threshold", label="阈值", type="percent",
                        default=0.08, min=0.0, max=0.3, step=0.01,
                        unit="小数", description="≥该振幅视为满足"),
    )
    buckets: tuple[Bucket, ...] = (
        Bucket(label="≥8%", predicate=lambda v, p: v >= p["threshold"]),
        Bucket(label="<8%", predicate=lambda v, p: v < p["threshold"]),
    )

    def compute(self, ctx: FactorContext) -> FactorResult: ...
```

内置因子位于 `app/factors/builtin/`（amplitude / boards / close_pos / low_time /
next_day_close / one_word / open_pct / shape / volume_ratio / wave_trend …），
新因子加一个模块并在 `builtin/__init__.py` 聚合 import 即完成注册。

## 2. 关键规则

1. **阈值即参数**：`FactorParamSpec(type="percent")` 为小数口径（0.08 = 8%）；
   档位划分（`Bucket`）也从参数读取，不写死常量。
2. **参数版本化**：参数存 `factor_configs`（JSONB），三态 `draft / active /
   archived`；admin 改参数 = 写入新 `active` 版本，历史自动归档；
   **回滚 = 以历史内容新建一个 active 版本**（不修改历史行）。
3. **解析顺序**：运行时覆盖（contextvars，任务隔离）> DB active 配置 > 代码默认；
   非法值回退代码默认并写 `factor_param_invalid` 结构化告警。
4. **热生效**：采集/选股下一轮任务即读到新参数，**无需重启任何进程**。
5. **结果缓存**：按 `factor_id + 参数版本 + 数据日期` 为键缓存（`factors/cache.py`），
   配置版本变化自动换键失效。

> 示例：把「首阴振幅」阈值从 8% 改为 7%，只需在前端「量化配置 → 因子」提交
> 一个新参数版本并启用；回滚点开历史版本「回滚」即可。

## 3. 有效性统计（`app/factors/stats.py`）

- 统一口径 `effectiveness(...)`：按**因子档位 × A/B/C 三段**输出
  样本数 / 期望收益 / 胜率 / 分段表现；
- 三段切分 `segment_cuts` 取数据中位数快照（A/B/C 各自独立评估，不只报全量）；
- 数据缺失时输出 `DEGRADED_BUCKET="数据缺失"` 档位，未落入声明档位输出
  `UNMATCHED_BUCKET="其他"`，均不静默丢弃样本；
- 暴露接口：`GET /api/factors/{factor_id}/effectiveness`（前端
  `FactorEffectiveness` 组件与 Agent 工具 `get_factor_effectiveness` 均消费）。

## 4. 接入步骤

1. 在 `app/factors/builtin/` 新增因子模块（纯函数 + 参数 schema + 档位声明）；
2. `builtin/__init__.py` 聚合 import（`register_factor` 在导入期完成注册与
   schema 导出，重复 `factor_id` 启动报错）；
3. 策略侧经 `factor_id + 参数版本` 引用（策略 `params_schema` 只声明自己的参数，
   因子阈值不要复制进策略）；
4. 验证：`pytest tests/test_factors.py`（含参数解析、档位、缓存键、统计口径）。

## 5. 定义同步与前端

启动时 `export_schemas()` 把全部因子定义 upsert 进 `factor_defs`
（含 params_schema / buckets），前端「量化配置 → 因子」自动渲染参数表单
（`ParamForm` 复用与策略同一渲染器）、版本历史与 diff 视图（`VersionHistoryPanel`
/ `VersionDiffView`）。
