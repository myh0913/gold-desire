"""连板捉妖周期门控（L1）——追涨路径，冰点/退潮禁入。

quant ``cycle.py::_GATE`` 口径：冰点（ICE）与退潮（RETREAT）期情绪冰封，
连板接力失败率高，全策略禁入；其余周期态放行（position_factor=1.0）。
"""

from __future__ import annotations

from collections.abc import Mapping

from app.strategies import CycleState, GateRule

#: 禁入周期态：冰点、退潮。
GATED_STATES = frozenset({CycleState.ICE, CycleState.RETREAT})

#: 全七周期态显式门控矩阵（未列态回退告警，故显式覆盖全集）。
GATE_MATRIX: Mapping[CycleState, GateRule] = {
    state: GateRule(False, 0.0) if state in GATED_STATES else GateRule(True, 1.0)
    for state in CycleState
}
