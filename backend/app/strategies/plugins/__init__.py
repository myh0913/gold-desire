"""策略插件目录：**新增策略 = 在本目录新增一个包/模块**。

生产发现（:func:`app.strategies.loader.discover_plugins`）只扫描本目录；放入本目录的策略
包/模块会被自动导入、收集 :class:`~app.strategies.protocol.BaseStrategy` 子类并注册，
**无需修改任何核心文件**。

插件约定：

- 一个策略一个包（目录 + ``__init__.py``）或一个顶层 ``*.py``；
- 包内定义 ``BaseStrategy`` 子类并声明非空 ``strategy_id``（可选加 ``@register_strategy``
  显式注册；不加也会被自动发现收集）；
- 门控矩阵写在策略自身的 ``gate_matrix``，核心不维护策略名清单；
- 禁止 import ``app.repositories`` / ``app.datasources`` / 具体 provider（架构测试守护），
  一切依赖经 ``StrategyContext`` 注入。

参见 ``app/strategies/examples/`` 的最小实现（该目录不参与生产发现）。
"""
