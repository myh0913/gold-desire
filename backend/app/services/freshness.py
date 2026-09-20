"""读路径数据新鲜度（``stale``）判定。

spec 要求：**上游不可用时读接口仍正常返回库中已有数据，并以 ``stale`` 标记**。
本模块把「库中最新入库时间」与可配置的期望新鲜度窗口比较：

- 窗口可通过环境变量 ``GD_MARKET_FRESHNESS_MINUTES`` 覆盖（默认 720 分钟 = 12h）；
- 无任何入库记录（或所需日期数据缺失）时判定为 ``stale=True``；
- 判定**只读库**，不触发任何上游调用，也不扫描文件系统。
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.repositories.reads import ReadRepository

__all__ = [
    "DEFAULT_FRESHNESS_MINUTES",
    "FRESHNESS_ENV",
    "freshness_window_minutes",
    "is_stale",
    "market_freshness",
]

FRESHNESS_ENV = "GD_MARKET_FRESHNESS_MINUTES"
"""覆盖期望新鲜度窗口（分钟）的环境变量名。"""

DEFAULT_FRESHNESS_MINUTES = 720
"""默认新鲜度窗口：12 小时（覆盖隔夜 + 盘后入库的常规节奏）。"""


def freshness_window_minutes() -> int:
    """返回生效的新鲜度窗口（分钟）；环境变量非法时回退默认值。"""
    raw = os.environ.get(FRESHNESS_ENV)
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            return DEFAULT_FRESHNESS_MINUTES
    return DEFAULT_FRESHNESS_MINUTES


def is_stale(
    latest_ingested_at: datetime | None,
    *,
    now: datetime | None = None,
    window_minutes: int | None = None,
) -> bool:
    """比较最近入库时间与新鲜度窗口，返回是否已过期。

    Args:
        latest_ingested_at: 库中最近入库时间；``None``（无数据）视为过期。
        now: 当前时间；``None`` 用 UTC 当前时间。
        window_minutes: 窗口分钟数；``None`` 读 :func:`freshness_window_minutes`。
    """
    if latest_ingested_at is None:
        return True
    moment = now if now is not None else datetime.now(UTC)
    ingested = (
        latest_ingested_at
        if latest_ingested_at.tzinfo is not None
        else latest_ingested_at.replace(tzinfo=UTC)
    )
    window = timedelta(
        minutes=window_minutes if window_minutes is not None else freshness_window_minutes()
    )
    return (moment - ingested) > window


async def market_freshness(
    read: ReadRepository,
    model: type[Any],
    *,
    date_column: str | None = "trade_date",
) -> tuple[bool, date | None]:
    """计算某行情表的 ``(stale, data_date)``。

    Args:
        read: 附加只读仓储。
        model: ORM 模型（须含 ``ingested_at``）。
        date_column: 数据日期列名；``None`` 表示该表无交易日列（如 ``stocks``）。
    """
    ingested = await read.latest_ingested_at(model)
    data_date = await read.latest_date(model, date_column) if date_column else None
    stale = is_stale(ingested)
    if date_column is not None and data_date is None:
        stale = True
    return stale, data_date
