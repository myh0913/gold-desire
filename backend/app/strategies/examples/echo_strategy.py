"""示例策略：演示 :class:`~app.strategies.protocol.BaseStrategy` 的最小实现。

要点（可作为新增策略的模板）：

1. 声明元数据：``strategy_id`` / ``label`` / ``version`` / ``description``；
2. 用 ``phases`` 声明参与阶段（无需核心维护策略名清单）；
3. 用 ``params_schema`` 暴露可配置参数（与因子同形，前端可自动渲染表单）；
4. 用 ``gate_matrix`` **自己声明**情绪周期门控（核心不再硬编码 ``_GATE``）；
5. 实现所需生命周期钩子（其余默认 no-op），依赖一律经 ``ctx`` 注入。

> 本文件位于 ``examples/``，**不参与生产发现**（发现只扫 ``plugins/``）；要上线只需把
> 本文件移入 ``app/strategies/plugins/``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.strategies.context import StrategyContext
from app.strategies.protocol import (
    BaseStrategy,
    CycleState,
    GateRule,
    Phase,
    StrategyParamSpec,
)


class EchoStrategy(BaseStrategy):
    """最小示例：把候选池原样回显（仅用于演示协议用法）。"""

    strategy_id = "example_echo"
    label = "示例·回显"
    version = "0.1.0"
    description = "演示最小策略实现；examples/ 不参与生产发现。"

    phases: ClassVar[frozenset[Phase]] = frozenset({Phase.POOL})

    params_schema: ClassVar[tuple[StrategyParamSpec, ...]] = (
        StrategyParamSpec(
            key="threshold",
            label="回显阈值",
            type="float",
            default=0.5,
            min=0.0,
            max=1.0,
            step=0.05,
            unit="小数",
            description="演示用阈值；仅当大于 0 时回显候选。",
        ),
    )

    gate_matrix: ClassVar[Mapping[CycleState, GateRule]] = {
        CycleState.ICE: GateRule(allowed=False, position_factor=0.0),
        CycleState.TURN: GateRule(allowed=True, position_factor=0.3),
        CycleState.REPAIR: GateRule(allowed=True, position_factor=0.5),
        CycleState.ACCEL: GateRule(allowed=True, position_factor=1.0),
        CycleState.DIVERGE: GateRule(allowed=True, position_factor=0.5),
        CycleState.RETREAT: GateRule(allowed=False, position_factor=0.0),
    }

    async def build_pool(self, ctx: StrategyContext) -> Any:
        """盘后建池：按阈值回显 ``strategy_id``（演示 ``get_params`` 用法）。"""
        params = await self.get_params(ctx)
        return [ctx.strategy_id] if float(params["threshold"]) > 0 else []
