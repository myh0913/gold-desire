"""时间口径工具：把交易日换算成上游所需的时间戳。

上游时间戳统一为 **Unix 毫秒**（hithink 的 ``start`` / ``end`` / ``date_ms`` 皆然），
且以业务时区（``Asia/Shanghai``）当日 ``00:00`` 为零点——与老项目
``quant-system`` 的 ``date_ms`` 口径一致，避免跨时区换算产生「差一天」。

用法::

    from app.core.timeutil import date_ms

    start_ms = date_ms(date(2026, 9, 18))
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

__all__ = ["SHANGHAI", "date_ms", "day_end_ms"]

#: 业务统一时区（与映射层 ``_SHANGHAI`` 同源）。
SHANGHAI = ZoneInfo("Asia/Shanghai")


def date_ms(value: date) -> int:
    """``date`` → 当日 ``00:00``（Asia/Shanghai）的 Unix **毫秒**时间戳。"""
    moment = datetime(value.year, value.month, value.day, tzinfo=SHANGHAI)
    return int(moment.timestamp() * 1000)


def day_end_ms(value: date) -> int:
    """``date`` → 当日 ``23:59:59.999``（Asia/Shanghai）的 Unix 毫秒时间戳。

    上游区间为**闭区间**，以「当日最后一毫秒」作上界可确保包含当日那根日线；
    若直接用 :func:`date_ms`（当日 00:00）作上界，当日数据会被排除在外。
    """
    return date_ms(value) + 86_400_000 - 1
