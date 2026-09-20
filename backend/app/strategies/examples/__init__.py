"""策略示例目录：演示最小可用实现，**不参与生产发现**。

与 ``plugins/`` 的区别与原因：

- ``plugins/`` 是生产插件目录，:func:`app.strategies.loader.discover_plugins` 会扫描它，
  其中的策略会被注册、同步进 ``strategy_defs`` 并参与阶段运行；
- ``examples/`` **不在**发现范围内（发现只扫 ``plugins/``），因此示例不会被注册，
  不会污染 ``strategy_defs`` 与阶段运行，也不受「重复 strategy_id」约束；
- 要让示例上线：把对应文件/目录移入 ``app/strategies/plugins/`` 即可，核心零改动。

``docs/extend-strategy.md``（Task 16）可直接指向本目录的 :class:`EchoStrategy` 作为
「最小实现」参考。
"""

from app.strategies.examples.echo_strategy import EchoStrategy

__all__ = ["EchoStrategy"]
