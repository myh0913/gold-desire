"""能力契约导出与「能力名 → 契约模型」注册表。

``CAPABILITY_CONTRACTS`` 是唯一事实来源：provider 声明能力、mapping 注册字段、
resolve 校验记录时都以此为准。启动断言据此检查「每个声明能力都有契约」。
"""

from __future__ import annotations

from app.datasources.contracts.base import (
    DEGRADED,
    ContractModel,
    ContractValidationError,
    ValidationIssue,
    validate_records,
)
from app.datasources.contracts.models import (
    DailyBarContract,
    LadderRowContract,
    LimitUpStockContract,
    MarketSentimentContract,
    MinuteBarContract,
    MonitorStockContract,
    NewsFlashContract,
    OpeningMatchContract,
    ThemeRankContract,
    ThemeStockContract,
    TradingDayContract,
)

__all__ = [
    "CAPABILITY_CONTRACTS",
    "DEGRADED",
    "ContractModel",
    "ContractValidationError",
    "DailyBarContract",
    "LadderRowContract",
    "LimitUpStockContract",
    "MarketSentimentContract",
    "MinuteBarContract",
    "MonitorStockContract",
    "NewsFlashContract",
    "OpeningMatchContract",
    "ThemeRankContract",
    "ThemeStockContract",
    "TradingDayContract",
    "ValidationIssue",
    "validate_records",
]

#: 能力名 → 契约模型。能力名沿用原项目 registry 口径，便于迁移对账。
CAPABILITY_CONTRACTS: dict[str, type[ContractModel]] = {
    "daily_bars": DailyBarContract,
    "limit_up_pool": LimitUpStockContract,
    "ladder": LadderRowContract,
    "trading_calendar": TradingDayContract,
    "market_sentiment": MarketSentimentContract,
    "theme_rank": ThemeRankContract,
    "theme_stocks": ThemeStockContract,
    "newsflash": NewsFlashContract,
    "minute_bars": MinuteBarContract,
    "opening_match": OpeningMatchContract,
    # 监管名单：两个端点形状不同 → 两个能力，共用同一份领域契约。
    "monitor_stocks": MonitorStockContract,
    "monitor_unusual": MonitorStockContract,
}
