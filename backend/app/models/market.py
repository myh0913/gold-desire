"""标准化行情与情绪模型（``std_*`` 语义）。

字段命名遵循领域标准英文 snake_case + 显式单位，SHALL NOT 沿用任何上游源的字段名。

单位约定：

- 价格/比率类字段单位为「元」或小数口径（如 ``0.0812`` 表示 8.12%），列注释中标注。
- ``volume_shares`` 单位：股；``volume_lots`` 单位：手；``*_yuan`` 单位：元。
- 时间标签 ``HH:MM``（``String(5)``，Asia/Shanghai）。

大表（``daily_bars`` / ``minute_bars`` / ``limit_up_pool`` / ``pool_snapshot`` /
``market_sentiment``）在 PostgreSQL 上按 ``trade_date`` 做月分区，见 ``app/db/partitions.py``。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JsonType, SourceMixin

# --------------------------------------------------------------------------- 基础信息


class Stock(SourceMixin, Base):
    """股票基础信息（证券代码为主键，全市场唯一）。"""

    __tablename__ = "stocks"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, doc="证券代码")
    name: Mapped[str] = mapped_column(String(64), nullable=False, doc="证券简称")
    market: Mapped[str] = mapped_column(String(16), nullable=False, doc="市场，如 SH/SZ/BJ")
    board: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="板块，如 主板/创业板/科创板"
    )
    is_st: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), doc="是否 ST"
    )
    list_date: Mapped[date | None] = mapped_column(Date, nullable=True, doc="上市日期")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --------------------------------------------------------------------------- 行情


class DailyBar(SourceMixin, Base):
    """个股日线行情。

    单位：价格字段为元；``volume_shares`` 股；``amount_yuan`` 元。
    """

    __tablename__ = "daily_bars"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_daily_bars_code_trade_date"),
        Index("ix_daily_bars_trade_date_code", "trade_date", "code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, doc="证券代码")
    trade_date: Mapped[date] = mapped_column(Date, index=True, nullable=False, doc="交易日")
    open: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, doc="开盘价（元）")
    high: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, doc="最高价（元）")
    low: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, doc="最低价（元）")
    close: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, doc="收盘价（元）")
    pre_close: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True, doc="前收盘价（元）；源缺失为 NULL"
    )
    volume_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, doc="成交量（股）")
    amount_yuan: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, doc="成交额（元）")


class MinuteBar(SourceMixin, Base):
    """个股分时行情（240 个交易分钟，索引 0..239）。

    单位：``price`` 元；``volume_lots`` 手；``amount_yuan`` 元。
    """

    __tablename__ = "minute_bars"
    __table_args__ = (
        UniqueConstraint(
            "code", "trade_date", "minute_index", name="uq_minute_bars_code_trade_date_minute"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, doc="证券代码")
    trade_date: Mapped[date] = mapped_column(Date, index=True, nullable=False, doc="交易日")
    minute_index: Mapped[int] = mapped_column(Integer, nullable=False, doc="分钟序号 0..239")
    time_label: Mapped[str] = mapped_column(String(5), nullable=False, doc="分钟标签 HH:MM")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, doc="该分钟收盘价（元）")
    volume_lots: Mapped[int] = mapped_column(BigInteger, nullable=False, doc="该分钟成交量（手）")
    amount_yuan: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, doc="该分钟成交额（元）"
    )


# --------------------------------------------------------------------------- 涨停 / 情绪


class LimitUpPool(SourceMixin, Base):
    """涨停池（含跌停/炸板/昨涨停/强势/新股等池型，由 ``pool_type`` 区分）。

    单位：``*_yuan`` 元；``turnover_rate`` 小数口径（0.0812 = 8.12%）。

    可选列说明：不同源的可用列不一致（hithink 无换手率/成交额/市值，
    xuangutong 无封单金额），缺失写 NULL 而非 0。
    """

    __tablename__ = "limit_up_pool"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "pool_type", "code", name="uq_limit_up_pool_trade_date_pool_code"
        ),
        Index("ix_limit_up_pool_trade_date_continue_days", "trade_date", "continue_days"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True, nullable=False, doc="交易日")
    code: Mapped[str] = mapped_column(String(16), nullable=False, doc="证券代码")
    name: Mapped[str] = mapped_column(String(64), nullable=False, doc="证券简称")
    continue_days: Mapped[int] = mapped_column(Integer, index=True, nullable=False, doc="连板天数")
    limit_up_time: Mapped[str | None] = mapped_column(
        String(5), nullable=True, doc="封板时间 HH:MM"
    )
    seal_amount_yuan: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True, doc="封单金额（元）；源缺失为 NULL"
    )
    open_times: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="开板次数；源缺失为 NULL"
    )
    turnover_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True, doc="换手率（小数）；源缺失为 NULL"
    )
    amount_yuan: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True, doc="成交额（元）；源缺失为 NULL"
    )
    market_cap_yuan: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True, doc="流通市值（元）；源缺失为 NULL"
    )
    pool_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc="池型：limit_up/limit_down/broken/prev_limit_up/strong/new_stock/sub_new",
    )


class PoolSnapshot(SourceMixin, Base):
    """池快照原始载荷（按池名留档，便于回放与排障）。"""

    __tablename__ = "pool_snapshot"
    __table_args__ = (
        UniqueConstraint("trade_date", "pool_name", name="uq_pool_snapshot_trade_date_pool_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, doc="交易日")
    pool_name: Mapped[str] = mapped_column(String(64), nullable=False, doc="池名")
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, doc="池结构化载荷")


class MarketSentiment(SourceMixin, Base):
    """市场情绪指标（每交易日一行）。

    单位：``temperature`` 分（0..100）；``broken_rate`` / ``premium_rate`` 小数口径。
    """

    __tablename__ = "market_sentiment"
    __table_args__ = (UniqueConstraint("trade_date", name="uq_market_sentiment_trade_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, doc="交易日")
    temperature: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, doc="情绪温度")
    stage: Mapped[str | None] = mapped_column(
        String(32), nullable=True, doc="情绪周期阶段；未派生时为 NULL"
    )
    limit_up_count: Mapped[int] = mapped_column(Integer, nullable=False, doc="涨停家数")
    limit_down_count: Mapped[int] = mapped_column(Integer, nullable=False, doc="跌停家数")
    broken_board_count: Mapped[int] = mapped_column(Integer, nullable=False, doc="炸板家数")
    broken_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, doc="炸板率（小数）"
    )
    up_count: Mapped[int] = mapped_column(Integer, nullable=False, doc="上涨家数")
    down_count: Mapped[int] = mapped_column(Integer, nullable=False, doc="下跌家数")
    max_continue_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="最高连板数；源缺失为 NULL"
    )
    premium_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, doc="溢价率（小数）"
    )


# --------------------------------------------------------------------------- 资讯 / 主题


class NewsFlash(SourceMixin, Base):
    """快讯（``symbols`` / ``categories`` 为 JSON 数组）。"""

    __tablename__ = "news_flash"
    __table_args__ = (UniqueConstraint("ts", "title", name="uq_news_flash_ts_title"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False, doc="发布时间"
    )
    level: Mapped[str | None] = mapped_column(
        String(16), nullable=True, doc="重要级别；源缺失为 NULL"
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False, doc="标题")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, doc="摘要")
    symbols: Mapped[list[str]] = mapped_column(JsonType, nullable=False, doc="关联证券代码列表")
    categories: Mapped[list[str]] = mapped_column(JsonType, nullable=False, doc="分类标签列表")


class Theme(SourceMixin, Base):
    """主题（板块）强度榜。

    单位：``core_avg_pct`` 小数口径（0.0521 = 5.21%）。
    """

    __tablename__ = "themes"
    __table_args__ = (UniqueConstraint("trade_date", "name", name="uq_theme_trade_date_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, doc="交易日")
    rank: Mapped[int] = mapped_column(Integer, nullable=False, doc="强度排名（1 起）")
    name: Mapped[str] = mapped_column(String(64), nullable=False, doc="主题名")
    core_avg_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True, doc="核心成分平均涨幅（小数）；源缺失为 NULL"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, doc="主题说明")
    core_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="核心成分数量；源缺失为 NULL"
    )


class ThemeStock(SourceMixin, Base):
    """主题成分股（主题 × 个股当日明细）。

    单位：``price`` 元；``pct`` / ``turnover_rate`` 小数口径。
    """

    __tablename__ = "theme_stocks"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "theme_name", "code", name="uq_theme_stock_trade_date_theme_code"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, doc="交易日")
    theme_name: Mapped[str] = mapped_column(String(64), nullable=False, doc="主题名")
    code: Mapped[str] = mapped_column(String(16), nullable=False, doc="证券代码")
    name: Mapped[str] = mapped_column(String(64), nullable=False, doc="证券简称")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, doc="价格（元）")
    pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, doc="当日涨幅（小数）")
    turnover_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, doc="换手率（小数）"
    )
    continue_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="连板天数；非连板或源缺失为 NULL"
    )
    selected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="入选时间"
    )


class MonitorStock(SourceMixin, Base):
    """监管名单（重点监控 / 严重异常波动）。"""

    __tablename__ = "monitor_stocks"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "kind", "code", name="uq_monitor_stock_trade_date_kind_code"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, doc="交易日")
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="类型：key_monitor/severe_unusual"
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False, doc="证券代码")
    name: Mapped[str] = mapped_column(String(64), nullable=False, doc="证券简称")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True, doc="监控原因")


class LadderRow(SourceMixin, Base):
    """连板天梯（按交易日与连板高度组织）。"""

    __tablename__ = "ladder_rows"
    __table_args__ = (
        UniqueConstraint("trade_date", "code", name="uq_ladder_row_trade_date_code"),
        Index("ix_ladder_rows_trade_date_continue_days", "trade_date", "continue_days"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, doc="交易日")
    code: Mapped[str] = mapped_column(String(16), nullable=False, doc="证券代码")
    name: Mapped[str] = mapped_column(String(64), nullable=False, doc="证券简称")
    continue_days: Mapped[int] = mapped_column(Integer, nullable=False, doc="连板天数")
    first_seal_time: Mapped[str | None] = mapped_column(
        String(5), nullable=True, doc="首次封板时间 HH:MM"
    )


__all__ = [
    "DailyBar",
    "LadderRow",
    "LimitUpPool",
    "MarketSentiment",
    "MinuteBar",
    "MonitorStock",
    "NewsFlash",
    "PoolSnapshot",
    "Stock",
    "Theme",
    "ThemeStock",
]
