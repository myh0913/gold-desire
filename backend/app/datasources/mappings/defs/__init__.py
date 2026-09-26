"""映射定义包：内置 Python 注册（fake/hithink/xuangutong/eltdx/eastmoney）与 YAML 定义共存。

导入本包即注册全部内置 Python 映射——``mappings.load_builtin_mappings`` 会导入本包，
随后再加载 ``*.yaml``（YAML 不覆盖 Python 定义）。
"""

from __future__ import annotations

from app.datasources.mappings.defs import eastmoney, eltdx, fake, hithink, xuangutong

__all__ = ["eastmoney", "eltdx", "fake", "hithink", "xuangutong"]

fake.register_fake_mappings()
hithink.register_hithink_mappings()
xuangutong.register_xuangutong_mappings()
eltdx.register_eltdx_mappings()
eastmoney.register_eastmoney_mappings()
