"""连板捉妖插件（L1 lianban_a）。

涨停池 2/3 板 + 六道结构过滤 + 竞价深低开买入的顺风追涨路径：

- ``strategy``：策略实现（POOL 盘后扫池候选 + OPENING 环境否决与场景 A 确认）；
- ``filters``：结构过滤纯函数（t11 回测口径复刻）；
- ``gates``：周期门控（冰点/退潮禁入）；
- 口径来源：emotion-cycle ``docs/strategy-matrix-final.md`` §二.1。
"""

from app.strategies.plugins.lianban.strategy import Advice, LianbanStrategy

__all__ = ["Advice", "LianbanStrategy"]
