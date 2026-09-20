"""龙回头策略插件包（``strategy_id="dragon"``）。

目录式插件：本包被 :func:`app.strategies.loader.discover_plugins` 自动发现，
其中的 :class:`~app.strategies.protocol.BaseStrategy` 子类被注册，核心零改动。

模块职责：

- :mod:`~app.strategies.plugins.dragon.gates` — 周期门控矩阵 + 两路硬门槛声明；
- :mod:`~app.strategies.plugins.dragon.bonus` — 两路加分项声明（S4 可加码 / S2 仅展示）；
- :mod:`~app.strategies.plugins.dragon.paths` — 两路（买点 / 卖出 / 仓位）定义；
- :mod:`~app.strategies.plugins.dragon.strategy` — 策略实现与结构化建议输出。
"""

from app.strategies.plugins.dragon.strategy import Advice, DragonStrategy

__all__ = ["Advice", "DragonStrategy"]
