"""回测与执行引擎：样本构建、卖出规则、三段切分、组合风控与分段回测。

- :mod:`app.engine.sell_rules` — 无止盈 + 3% 止损 + 持 1 可卖日（readme §6 / §14）；
- :mod:`app.engine.guards` — 盘中 / 竞价撮合价除权伪跳变护栏（越界整票拒收，readme §2.5）；
- :mod:`app.engine.dragon_samples` — 从库内日线 + 分时构建龙回头样本（生产路径）；
- :mod:`app.engine.dragon_legacy` — 已验证样本夹具适配器（**仅用于复现基线数值**）；
- :mod:`app.engine.segments` — A/B/C 三段互不重叠切分（readme §1.5 / §14.7）；
- :mod:`app.engine.portfolio` — 路径/门槛/加分项声明与组合模拟（readme §7）；
- :mod:`app.engine.backtest` — 分段回测报告与落库（readme §7.4）。
"""
