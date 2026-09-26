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
from typing import Any, Literal

from pydantic import Field

from app.datasources.contracts.base import ContractModel

__all__ = [
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
]

#: 监管名单类型：``restricted``=交易所重点监控 / ``severe``=严重异常波动 /
#: ``unusual``=普通异常波动（口径对齐参考实现 ``MonitorStock.monitor_type``）。
MonitorKind = Literal["restricted", "severe", "unusual"]

#: 池类型：与上游 ``pool_name`` 口径一致（7 种）。
PoolType = Literal[
    "limit_up",  # 涨停池
    "limit_up_broken",  # 炸板池
    "yesterday_limit_up",  # 昨涨停
    "super_stock",  # 强势股
    "limit_down",  # 跌停池
    "new_stock",  # 新股
    "nearly_new",  # 次新
]


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
    （如 hithink 无换手率/成交额/市值/涨停原因，xuangutong 无封单金额），
    缺失一律写 ``None``，SHALL NOT 用 0 顶替。
    """

    code: str = Field(description="证券代码，标准形如 600519.SH")
    name: str = Field(description="证券简称")
    continue_days: int = Field(
        ge=0,
        description="连板天数，``0`` 表示当日未处于连板状态"
        "（炸板池 / 跌停池 / 新股 / 次新 / 非连板的强势股均可能为 0）",
    )
    limit_up_time: str | None = Field(
        default=None, description="首次封板时间，格式 HH:MM；缺失为 None"
    )
    seal_amount_yuan: float | None = Field(
        default=None, ge=0, description="封单金额，单位：元；源缺失为 None"
    )
    max_seal_amount_yuan: float | None = Field(
        default=None, ge=0, description="盘中最大封单金额，单位：元；源缺失为 None"
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
    price: float | None = Field(default=None, ge=0, description="现价，单位：元；源缺失为 None")
    change_pct: float | None = Field(
        default=None, description="涨跌幅，单位：小数（0.1001=+10.01%）；源缺失为 None"
    )
    volume_bias_ratio: float | None = Field(
        default=None, ge=0, description="量比；源缺失为 None"
    )
    free_cap_yuan: float | None = Field(
        default=None, ge=0, description="流通市值，单位：元；源缺失为 None"
    )
    seal_ratio: float | None = Field(
        default=None, ge=0, description="封单比（买盘封单量/流通股，小数）；源缺失为 None"
    )
    reason: str | None = Field(
        default=None, description="涨停原因（上游口语化说明）；源缺失为 None"
    )
    plates: list[dict[str, Any]] | None = Field(
        default=None,
        description="关联板块 ``[{\"plate_id\": int, \"plate_name\": str}]``；源缺失为 None",
    )
    timeline: list[dict[str, Any]] | None = Field(
        default=None,
        description="封板时间线 ``[{\"timestamp\": int, \"status\": int}]``"
        "（status: 1 封涨停 / 2 炸板 / 3 封跌停 / 4 开跌停）；源缺失为 None",
    )
    pool_type: PoolType = Field(description="池类型：与上游 pool_name 口径一致的 7 种")
    list_date: date | None = Field(
        default=None, description="上市日期；源缺失为 None（供 stocks 表顺带补写）"
    )


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
    title: str = Field(description="标题")
    summary: str = Field(default="", description="摘要，源缺失时为空串")
    symbols: list[str] = Field(default_factory=list, description="关联证券代码列表")
    categories: list[str] = Field(default_factory=list, description="分类/标签列表")


class MinuteBarContract(ContractModel):
    """个股分时分钟点契约（240 个交易分钟，索引 0..239）。

    口径对齐旧项目 eltdx 分时（``DATA_CONTRACT.md`` / analyzer 注释）：
    ``time_label`` 为**零填充 ``"HH:MM"``**（09:31~11:30 → 0..119，
    13:01~15:00 → 120..239，字符串比较安全）；``volume_lots`` 单位**手**
    （eltdx 分钟增量为手，与 hithink 日线「股」换算系数 100）。
    ``amount_yuan`` 仅部分源提供，缺失为 ``None``（不估算）。
    """

    code: str = Field(description="证券代码，标准形如 600519.SH")
    trade_date: date = Field(description="交易日（Asia/Shanghai）")
    minute_index: int = Field(ge=0, le=239, description="分钟序号 0..239")
    time_label: str = Field(description="分钟标签，零填充 HH:MM（如 09:31 / 14:45）")
    price: float = Field(gt=0, description="该分钟收盘价，单位：元")
    volume_lots: int = Field(ge=0, description="该分钟成交量，单位：手")
    amount_yuan: float | None = Field(
        default=None, ge=0, description="该分钟成交额，单位：元；源缺失为 None"
    )


class OpeningMatchContract(ContractModel):
    """09:25 集合竞价正式撮合契约（单只股票单日，无撮合则无记录）。"""

    code: str = Field(description="证券代码，标准形如 600519.SH")
    trade_date: date = Field(description="交易日（Asia/Shanghai）")
    price: float = Field(gt=0, description="撮合价（即当日开盘价），单位：元")
    volume_lots: int | None = Field(
        default=None, ge=0, description="撮合成交量，单位：手；源缺失为 None"
    )
    time_label: str | None = Field(
        default=None, description="撮合时间标签（如 09:25）；源缺失为 None"
    )


class AuctionSeriesContract(ContractModel):
    """个股集合竞价时序点契约（09:15~09:25 竞价阶段的逐点快照）。

    口径对齐 eltdx auctions（``time_label`` **带秒**，如 ``"09:20:03"``），
    与分时的零填充 ``"HH:MM"`` 不同。竞价撮合量单位**手**；撮合额为估算值
    （价格 × 手 × 100），源无法给出真实分笔金额，缺失为 ``None``。
    消费方（J1 竞价抢筹）取**第一个 ``time_label`` 以 ``"09:20"`` 开头**的点
    作为 9:20 参考价。
    """

    code: str = Field(description="证券代码，标准形如 600519.SH")
    trade_date: date = Field(description="交易日（Asia/Shanghai）")
    time_label: str = Field(description="竞价时点标签，带秒（如 09:20:03）")
    price: float = Field(gt=0, description="该时点虚拟撮合价，单位：元")
    matched_volume_lots: int | None = Field(
        default=None, ge=0, description="该时点虚拟撮合量，单位：手；源缺失为 None"
    )
    matched_amount_yuan: float | None = Field(
        default=None,
        ge=0,
        description="该时点虚拟撮合额（估算=价×手×100），单位：元；源缺失为 None",
    )


class MonitorStockContract(ContractModel):
    """监管名单单条契约（某交易日的重点监控 / 异常波动记录）。

    三个来源共用本契约，由 ``kind`` 区分（见 :data:`MonitorKind`）：

    - ``restricted``（交易所重点监控）：有监控**有效期**（``start_date`` /
      ``end_date``）与公告链接（``link_url``），**无**公告原因文本；
    - ``severe`` / ``unusual``（严重 / 普通异常波动）：有异动区间
      （``start_date`` / ``end_date``）、公告日（``notice_date``）、公告编号
      （``info_code``）、原因文本（``reason``）与原因分类（``reason_type``）。

    故除 ``trade_date`` / ``kind`` / ``code`` / ``name`` 外的字段**一律可选**：
    缺失写 ``None``，不用 0 / 空串顶替。
    """

    trade_date: date = Field(description="交易日（Asia/Shanghai）")
    kind: MonitorKind = Field(description="名单类型：restricted / severe / unusual")
    code: str = Field(description="证券代码，标准形如 600519.SH")
    name: str = Field(description="证券简称")
    reason: str | None = Field(default=None, description="监控/异动原因；源缺失为 None")
    start_date: date | None = Field(
        default=None, description="起始日：重点监控的监控期起 / 异动区间起；源缺失为 None"
    )
    end_date: date | None = Field(
        default=None, description="截止日：重点监控的监控期止 / 异动区间止；源缺失为 None"
    )
    notice_date: date | None = Field(default=None, description="公告日；源缺失为 None")
    info_code: str | None = Field(default=None, description="公告编号；源缺失为 None")
    reason_type: str | None = Field(default=None, description="原因分类；源缺失为 None")
    link_url: str | None = Field(default=None, description="公告链接；源缺失为 None")
