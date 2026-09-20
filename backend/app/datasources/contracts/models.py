"""Phase-1 能力契约模型（领域标准名 + 显式单位）。

每个模型对应一个「能力」（capability），字段名一律使用领域标准名，禁止出现
任何具体源的命名（如 ``continue_day_cnt`` / ``last_price``）。所有数值字段的
docstring 显式声明单位，便于跨源对账与因子计算。

单位约定：

- 股数：``*_shares``（股，非手）
- 金额：``*_yuan``（元）
- 比率/涨跌幅：``*_rate`` / ``*_pct``（小数，0.0125 表示 1.25%）
- 时间点：``*_time`` 为 ``"HH:MM"`` 字符串
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.datasources.contracts.base import ContractModel

__all__ = [
    "DailyBarContract",
    "LadderRowContract",
    "LimitUpStockContract",
    "MarketSentimentContract",
    "NewsFlashContract",
    "ThemeRankContract",
    "ThemeStockContract",
    "TradingDayContract",
]

PoolType = Literal["limit_up", "limit_down", "broken"]


class DailyBarContract(ContractModel):
    """日线行情契约（单只股票单日，不复权）。"""

    code: str = Field(description="证券代码，标准形如 600519.SH / 000001.SZ / 830799.BJ")
    trade_date: date = Field(description="交易日（Asia/Shanghai）")
    open: float = Field(description="开盘价，单位：元")
    high: float = Field(description="最高价，单位：元")
    low: float = Field(description="最低价，单位：元")
    close: float = Field(description="收盘价，单位：元")
    pre_close: float | None = Field(default=None, description="昨收价，单位：元；源缺失为 None")
    volume_shares: int = Field(ge=0, description="成交量，单位：股")
    amount_yuan: float = Field(ge=0, description="成交额，单位：元")


class LimitUpStockContract(ContractModel):
    """涨停/跌停/炸板池条目契约。

    可选字段遵循「能算的算、算不出的留空」：不同源提供的列不一致
    （如 hithink 无换手率/成交额/市值，xuangutong 无封单金额），
    缺失一律写 ``None``，SHALL NOT 用 0 顶替。
    """

    code: str = Field(description="证券代码，标准形如 600519.SH")
    name: str = Field(description="证券简称")
    continue_days: int = Field(ge=1, description="连板天数（自然连板数，≥1）")
    limit_up_time: str | None = Field(
        default=None, description="首次封板时间，格式 HH:MM；缺失为 None"
    )
    seal_amount_yuan: float | None = Field(
        default=None, ge=0, description="封单金额，单位：元；源缺失为 None"
    )
    open_times: int | None = Field(
        default=None, ge=0, description="当日炸板次数，单位：次；源缺失为 None"
    )
    turnover_rate: float | None = Field(
        default=None, ge=0, description="换手率，单位：小数（0.125=12.5%）；源缺失为 None"
    )
    amount_yuan: float | None = Field(
        default=None, ge=0, description="成交额，单位：元；源缺失为 None"
    )
    market_cap_yuan: float | None = Field(
        default=None, ge=0, description="总市值，单位：元；源缺失为 None"
    )
    pool_type: PoolType = Field(description="池类型：limit_up / limit_down / broken")


class LadderRowContract(ContractModel):
    """连板天梯单行契约（某交易日某票的连板信息）。"""

    trade_date: date = Field(description="交易日（Asia/Shanghai）")
    code: str = Field(description="证券代码，标准形如 600519.SH")
    name: str = Field(description="证券简称")
    continue_days: int = Field(ge=1, description="连板天数，单位：天（≥1）")
    first_seal_time: str | None = Field(
        default=None, description="首次封板时间，格式 HH:MM；缺失为 None"
    )


class TradingDayContract(ContractModel):
    """交易日历条目契约。"""

    trade_date: date = Field(description="日期（Asia/Shanghai）")
    is_open: bool = Field(description="是否为交易日：True=开市")


class MarketSentimentContract(ContractModel):
    """市场情绪指标契约（按交易日聚合）。

    ``stage``（情绪周期阶段）不在任何单一源中提供：hithink 不吐该字段，
    xuangutong 只有原始指标。按既定策略「能算的算、算不出的留空」，
    该字段由策略层基于 :mod:`app.strategies` 的周期状态机派生；
    在派生实现落地前保持 ``None``。
    """

    trade_date: date = Field(description="交易日（Asia/Shanghai）")
    temperature: float = Field(ge=0, le=100, description="市场温度，单位：0-100 分")
    stage: str | None = Field(default=None, description="情绪周期阶段名；未派生时为 None")
    limit_up_count: int = Field(ge=0, description="涨停家数，单位：家")
    limit_down_count: int = Field(ge=0, description="跌停家数，单位：家")
    broken_board_count: int = Field(ge=0, description="炸板家数，单位：家")
    broken_rate: float = Field(ge=0, le=1, description="炸板率，单位：小数（0.15=15%）")
    up_count: int = Field(ge=0, description="上涨家数，单位：家")
    down_count: int = Field(ge=0, description="下跌家数，单位：家")
    max_continue_days: int | None = Field(
        default=None, ge=1, description="最高连板高度，单位：天；源缺失为 None"
    )
    premium_rate: float = Field(description="昨涨停溢价率，单位：小数（0.023=2.3%）")


class ThemeRankContract(ContractModel):
    """题材排名契约（当日题材强弱榜）。"""

    trade_date: date = Field(description="交易日（Asia/Shanghai）")
    rank: int = Field(ge=1, description="当日排名（1 起）")
    name: str = Field(description="题材名称")
    core_avg_pct: float | None = Field(
        default=None, description="核心股平均涨跌幅，单位：小数（0.052=5.2%）；源缺失为 None"
    )
    description: str | None = Field(default=None, description="题材说明；源缺失为 None")
    core_count: int | None = Field(
        default=None, ge=0, description="核心股数量，单位：只；源缺失为 None"
    )


class ThemeStockContract(ContractModel):
    """题材内个股契约。"""

    trade_date: date = Field(description="交易日（Asia/Shanghai）")
    theme_name: str = Field(description="所属题材名称")
    code: str = Field(description="证券代码，标准形如 002230.SZ")
    name: str = Field(description="证券简称")
    price: float = Field(description="最新价，单位：元")
    pct: float = Field(description="涨跌幅，单位：小数（0.0998=9.98%）")
    turnover_rate: float = Field(ge=0, description="换手率，单位：小数")
    continue_days: int | None = Field(
        default=None, ge=1, description="连板天数，单位：天；非连板或源缺失为 None"
    )


class NewsFlashContract(ContractModel):
    """新闻快讯契约。"""

    ts: datetime = Field(description="快讯时间（Asia/Shanghai，含时区）")
    level: str | None = Field(default=None, description="重要级别（源自定义，如 A/B/C）")
    title: str = Field(description="标题")
    summary: str = Field(default="", description="摘要，源缺失时为空串")
    symbols: list[str] = Field(default_factory=list, description="关联证券代码列表")
    categories: list[str] = Field(default_factory=list, description="分类/标签列表")
