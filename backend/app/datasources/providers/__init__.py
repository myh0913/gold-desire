"""具体数据源 provider 实现包。

新增数据源 = 在本目录新增一个 provider 文件（``@register_provider`` 装饰）
+ 在 ``mappings/defs/`` 新增一份映射；业务代码零改动。

注意：**包外的业务代码禁止 import 本目录下的具体模块**（由架构测试守护）。
"""

from __future__ import annotations

from app.datasources.providers.eltdx import EltdxProvider
from app.datasources.providers.fake import FakeProvider
from app.datasources.providers.hithink import HithinkProvider
from app.datasources.providers.xuangutong import XuangutongProvider
from app.datasources.registry import set_capability_order

__all__ = [
    "REAL_CAPABILITY_PROVIDERS",
    "EltdxProvider",
    "FakeProvider",
    "HithinkProvider",
    "XuangutongProvider",
    "install_real_capability_order",
]

#: 真实源接入后各能力的默认主备顺序（主源在前）。顺序依据参考项目
#: ``quant-system.datasource.registry.CAPABILITY_PROVIDERS`` 与 ``DATA_CONTRACT.md``：
#: hithink 为 daily_bars / ladder / trading_calendar / limit_up_pool 主源，
#: xuangutong 为 market_sentiment / theme_rank / theme_stocks / newsflash 主源，
#: eltdx 为 minute_bars / opening_match 主源（分时唯一真实源，无 HTTP 备源），fake 兜底。
#
# 说明：本体载入即通过 :func:`set_capability_order` 安装为「运行时覆盖」，
# 不改动 ``registry.CAPABILITY_PROVIDERS`` 默认表（保持全 fake），从而离线测试在未显式
# 声明顺序时始终取 fake，绝不误触上游网络。
REAL_CAPABILITY_PROVIDERS: dict[str, list[str]] = {
    "daily_bars": ["hithink", "fake"],
    # 涨停池首选选股通：该端点字段最全（现价/涨幅/量比/流通市值/涨停原因+关联板块/
    # 封板时间线/封单比），hithink 同能力字段较少（无涨停原因、现价、涨幅），
    # 保留为备源以便选股通故障时自动降级。
    "limit_up_pool": ["xuangutong", "hithink", "fake"],
    "ladder": ["hithink", "fake"],
    "trading_calendar": ["hithink", "fake"],
    "market_sentiment": ["xuangutong", "fake"],
    "theme_rank": ["xuangutong", "fake"],
    "theme_stocks": ["xuangutong", "fake"],
    "newsflash": ["xuangutong", "fake"],
    "minute_bars": ["eltdx", "fake"],
    "opening_match": ["eltdx", "fake"],
}


def install_real_capability_order() -> None:
    """把真实源主备顺序安装为能力级的运行时覆盖（幂等）。

    业务侧在应用装配时调用（本包导入时亦自动调用一次）；测试可在重置后重新调用。
    """
    for capability, order in REAL_CAPABILITY_PROVIDERS.items():
        set_capability_order(capability, order)


install_real_capability_order()
