"""采集时间窗口（数据化，可配置）。

窗口是**纯数据**（:class:`Window` 列表），不在调度逻辑里散落常量。默认值对齐
参考项目 ``quant_system.scheduler`` 的语义：

=========== =============== ========================================
窗口名       区间（HH:MM）    用途
=========== =============== ========================================
auction     09:25-09:40     竞价池（涨停池首封）+ 09:25 撮合价
intraday    09:26-10:00     盘中轮询（快讯等，按 interval 重复）
tailpan     14:45-15:00     尾盘题材（题材榜 / 题材个股）
postmarket  17:00-18:00     盘后日线 / 天梯 / 情绪 / 交易日历
intraday_day 09:30-15:00    全时段分时（minute_bars，5 分钟一轮）
=========== =============== ========================================

可配置性：本模块从环境变量读取覆盖值（``Settings`` 由其他 Task 持有，本 Task
不修改 ``app/core/config.py``）。优先级：显式传入的 ``settings`` 属性 >
环境变量 > 默认值。

- ``INGEST_WINDOW_<NAME>`` 形如 ``"09:25-09:40"``（``<NAME>`` 为大写窗口名）；
- ``INGEST_TICK_SECONDS`` 常驻调度 tick 间隔（秒，默认 15）；
- ``INGEST_CALENDAR_TTL_SECONDS`` 交易日历缓存 TTL（秒，默认 5 天）。

若未来 ``Settings`` 增加了 ``ingest_window_*`` / ``ingest_tick_seconds`` /
``ingest_calendar_ttl_seconds`` 字段，本模块会自动优先采用，无需改这里。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from app.core.config import Settings

__all__ = [
    "DEFAULT_WINDOWS",
    "Window",
    "in_window",
    "load_calendar_ttl",
    "load_tick_seconds",
    "load_windows",
    "window_by_name",
]


@dataclass(frozen=True, slots=True)
class Window:
    """采集窗口（闭区间，含起止 ``HH:MM``）。

    Attributes:
        name: 窗口名（auction/intraday/tailpan/postmarket）。
        start: 起始 ``HH:MM``（含）。
        end: 结束 ``HH:MM``（含）。
    """

    name: str
    start: str
    end: str

    @property
    def bounds(self) -> tuple[str, str]:
        """返回 ``(start, end)``，兼容参考项目 ``tuple[str, str]`` 口径。"""
        return (self.start, self.end)


DEFAULT_WINDOWS: tuple[Window, ...] = (
    Window("auction", "09:25", "09:40"),
    Window("intraday", "09:26", "10:00"),
    Window("tailpan", "14:45", "15:00"),
    Window("postmarket", "17:00", "18:00"),
    Window("intraday_day", "09:30", "15:00"),
)
"""默认窗口列表（参考项目语义的干净重写）。"""

DEFAULT_TICK_SECONDS = 15
"""常驻调度器默认 tick 间隔（秒）。"""

DEFAULT_CALENDAR_TTL_SECONDS = 5 * 24 * 3600
"""交易日历缓存默认 TTL（秒）——5 天，覆盖跨周末。"""


def _env_key(name: str) -> str:
    """窗口名的环境变量键，如 ``auction`` → ``INGEST_WINDOW_AUCTION``。"""
    return f"INGEST_WINDOW_{name.upper()}"


def _override(
    settings: Settings | None, attr: str, env: Mapping[str, str], env_key: str
) -> str | None:
    """按「settings 属性 > 环境变量」取覆盖值。"""
    if settings is not None:
        value = getattr(settings, attr, None)
        if value:
            return str(value)
    return env.get(env_key)


def load_windows(
    settings: Settings | None = None, *, environ: Mapping[str, str] | None = None
) -> list[Window]:
    """加载窗口列表（默认值 + 可配置覆盖）。

    Args:
        settings: 显式配置对象；缺省只看环境变量。
        environ: 环境变量映射（便于测试注入）；缺省读 ``os.environ``。
    """
    env = environ if environ is not None else os.environ
    windows: list[Window] = []
    for default in DEFAULT_WINDOWS:
        raw = _override(settings, f"ingest_window_{default.name}", env, _env_key(default.name))
        if raw:
            start, _, end = str(raw).partition("-")
            if start.strip() and end.strip():
                windows.append(Window(default.name, start.strip(), end.strip()))
                continue
        windows.append(default)
    return windows


def window_by_name(windows: list[Window], name: str) -> Window:
    """按名取窗口。

    Raises:
        KeyError: 窗口名不存在。
    """
    for window in windows:
        if window.name == name:
            return window
    raise KeyError(f"未声明的采集窗口: {name!r}（已声明：{[w.name for w in windows]}）")


def in_window(now: datetime, window: Window) -> bool:
    """``now`` 是否落在窗口闭区间内（按 ``HH:MM`` 比较）。"""
    hm = now.strftime("%H:%M")
    return window.start <= hm <= window.end


def _int_setting(
    settings: Settings | None,
    attr: str,
    env: Mapping[str, str],
    env_key: str,
    default: int,
) -> int:
    """读取整型配置（settings 属性 > 环境变量 > 默认值），非法值回退默认。"""
    raw = _override(settings, attr, env, env_key)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def load_tick_seconds(
    settings: Settings | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    default: int = DEFAULT_TICK_SECONDS,
) -> int:
    """加载 tick 间隔秒数（``INGEST_TICK_SECONDS``）。"""
    env = environ if environ is not None else os.environ
    return _int_setting(settings, "ingest_tick_seconds", env, "INGEST_TICK_SECONDS", default)


def load_calendar_ttl(
    settings: Settings | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    default: int = DEFAULT_CALENDAR_TTL_SECONDS,
) -> int:
    """加载交易日历缓存 TTL 秒数（``INGEST_CALENDAR_TTL_SECONDS``）。"""
    env = environ if environ is not None else os.environ
    return _int_setting(
        settings, "ingest_calendar_ttl_seconds", env, "INGEST_CALENDAR_TTL_SECONDS", default
    )
