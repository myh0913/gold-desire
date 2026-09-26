"""首板低吸插件（S1 firstboard_dip）。

低位横盘首板 + 次日深低开买入的逆势低吸路径：

- ``strategy``：策略实现（POOL 盘后扫描候选 + OPENING 次日开盘确认）；
- 口径来源：emotion-cycle ``docs/strategy-matrix-final.md`` §二.3。
"""

from app.strategies.plugins.firstboard_dip.strategy import Advice, FirstboardDipStrategy

__all__ = ["Advice", "FirstboardDipStrategy"]
