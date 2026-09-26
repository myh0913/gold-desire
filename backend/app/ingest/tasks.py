"""采集任务注册表（声明式）。

每个**能力**（capability）注册一个 :class:`IngestTaskDef`，声明：

- ``capability``：数据源能力名（见 ``app.datasources.contracts``）；
- ``target``：写入器键（:data:`WRITERS`），**唯一**决定「契约 → 目标表列」的映射，
  写入器集中在 :mod:`app.ingest.writers`，消费方零改动；
- ``window``：可选执行窗口（:class:`~app.ingest.windows.Window`，按名匹配）；
- ``interval_seconds``：窗口内重复执行间隔（0 = 窗口内只跑一次）；
- ``idempotency_key``：由 ``(capability, trade_date)`` 构造的幂等键；
- ``args_builder``：为给定交易日构造**一组**取数参数（逐份透传给 ``provider.fetch``）。

支持「一次任务多轮取数」：如 ``daily_bars`` 的上游按单只证券取数，故其
``args_builder`` 返回每票一份参数，执行器逐份取数并累加行数（见
:func:`app.ingest.pipeline.run_task`）。

任务状态（是否已完成）由调度器落库，见 :mod:`app.ingest.scheduler`。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.timeutil import date_ms, day_end_ms
from app.ingest.windows import Window
from app.ingest.writers import (
    AUCTION_SERIES_POOL_NAME,
    CALENDAR_POOL_NAME,
    OPENING_MATCH_POOL_NAME,
    WRITERS,
)
from app.repositories import Repositories

__all__ = [
    "AUCTION_SERIES_POOL_NAME",
    "CALENDAR_POOL_NAME",
    "DEFAULT_TASKS",
    "OPENING_MATCH_POOL_NAME",
    "WRITERS",
    "IngestTaskDef",
    "TaskRegistryError",
    "all_tasks",
    "get_task",
    "register_task",
    "reset_tasks",
]

#: 取数参数构造器：``(trade_date, repos) -> 一组参数``（每份对应一轮取数）。
ArgsBuilder = Callable[[date, Repositories], Awaitable[Sequence[dict[str, Any]]]]


class TaskRegistryError(RuntimeError):
    """任务注册表不一致（重名 / 写入器缺失）。"""


# ============================================================ 任务定义与注册表


@dataclass(frozen=True, slots=True)
class IngestTaskDef:
    """采集任务定义（声明式）。

    Attributes:
        name: 任务名（注册表键，唯一）。
        capability: 数据源能力名。
        target: 写入器键（:data:`WRITERS`）。
        window: 执行窗口；``None`` 表示任意时刻到期即执行（如交易日历）。
        interval_seconds: 窗口内重复间隔；``0`` 表示窗口内只执行一次。
        idempotency_key: ``(capability, trade_date) -> str`` 幂等键构造函数。
        args_builder: ``(trade_date, repos) -> 一组取数参数``；返回空序列表示本轮
            「无标的可采」，执行器按成功 0 行处理（不触达上游、不报错）。
        enabled: 是否启用（调度器跳过停用任务）。
    """

    name: str
    capability: str
    target: str
    window: Window | None
    interval_seconds: int
    idempotency_key: Callable[[str, date], str]
    args_builder: ArgsBuilder
    enabled: bool = True


def _default_key(capability: str, trade_date: date) -> str:
    """默认幂等键：``<capability>:<trade_date>``。"""
    return f"{capability}:{trade_date.isoformat()}"


async def _date_args(trade_date: date, repos: Repositories) -> list[dict[str, Any]]:
    """默认取数参数：``[{"date": "YYYY-MM-DD"}]``（选股通按 ``date`` 锁定历史日）。"""
    return [{"date": trade_date.isoformat()}]


#: 异常波动各类型的取数页数（上游按 ``NOTICE_DATE`` **倒序**，故第 1 页即最新）。
#: 实测（2026-09-22）：002 严重异常波动全量 179 条 / 4 页 → **取全**；
#: 001 普通异常波动全量 5499 条 / 110 页 → **只取最新 2 页（100 条）**，防止把表撑爆。
#: 两个数都是**显式上限**：上游历史会持续增长，页数不随 count 变化，避免采集量失控。
MONITOR_UNUSUAL_PAGES: dict[str, int] = {
    "severe": 4,
    "unusual": 2,
}


async def _monitor_stocks_args(trade_date: date, repos: Repositories) -> list[dict[str, Any]]:
    """重点监控取数参数：**单轮**（该端点只回最新名单，无分页、无历史）。"""
    return [{"date": trade_date.isoformat()}]


async def _monitor_unusual_args(trade_date: date, repos: Repositories) -> list[dict[str, Any]]:
    """异常波动取数参数：``severe`` / ``unusual`` 各按 :data:`MONITOR_UNUSUAL_PAGES` 逐页取数。

    ``kind`` 同时作为映射上下文（``args.kind``）落成表里的 ``kind`` 列：
    ``severe``=严重异常波动（002）、``unusual``=普通异常波动（001）。
    """
    return [
        {"kind": kind, "page": page, "date": trade_date.isoformat()}
        for kind, pages in MONITOR_UNUSUAL_PAGES.items()
        for page in range(1, pages + 1)
    ]


#: 涨停池 7 种池型（上游 ``pool_name``）；**顺序即前端 Tab 顺序**。
POOL_TYPES: tuple[str, ...] = (
    "limit_up",  # 涨停池
    "limit_up_broken",  # 炸板池
    "yesterday_limit_up",  # 昨涨停
    "super_stock",  # 强势股
    "limit_down",  # 跌停池
    "new_stock",  # 新股
    "nearly_new",  # 次新
)


async def _limit_up_pool_args(trade_date: date, repos: Repositories) -> list[dict[str, Any]]:
    """涨停池取数参数：**每种池型一组**（共 7 组，逐轮取数、逐池型替换写入）。

    参数口径（对齐参考实现 quant）：

    - ``pool_name``：池型，选股通按此返回对应池；
    - ``date``：**仅当目标交易日不是「今天」时**才传（历史回补路径）。当前交易日的
      轮询**不传 date**——上游带 date 会走「归档模式」，``nearly_new`` / ``new_stock``
      的归档口径与实时不一致（实测 89→23、2→1），而参考实现同样不传 date；
    - ``date_ms``：同花顺所需（该源只有涨停池，不支持池型）。

    注：hithink 不支持池型（只有涨停池）。若选股通故障回退到 hithink，7 轮都会写
    ``pool_type='limit_up'``（幂等覆盖，最终 state 仍是正确的涨停池，其余 6 个池型
    保持为空）——降级可接受，不报错。
    """
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    historical = trade_date != today
    return [
        {
            "pool_name": pool_type,
            "date_ms": date_ms(trade_date),
            **({"date": trade_date.isoformat()} if historical else {}),
        }
        for pool_type in POOL_TYPES
    ]


async def _limit_up_supplement_args(trade_date: date, repos: Repositories) -> list[dict[str, Any]]:
    """涨停池补数取数参数：hithink 单轮（该源只有涨停池，不支持池型）。

    与 :func:`_limit_up_pool_args` 的 7 轮不同，本任务只有 1 轮；``date_ms``
    为 hithink 端点的目标日期参数（当日实时 / 历史归档同参）。
    """
    return [{"date_ms": date_ms(trade_date)}]


#: 日线标的回看窗口（自然日）：从近期涨停池/天梯推导「策略相关票」。
DAILY_BAR_LOOKBACK_DAYS = 30


def _main_board_profile(code: str, name: str) -> dict[str, Any] | None:
    """由代码与简称推导 ``stocks`` 表行；**非沪深主板或 ST 返回 ``None``**。"""
    digits = code.split(".")[0]
    if len(digits) != 6 or not digits.isdigit():
        return None
    if digits.startswith("60"):
        market = "SH"
    elif digits.startswith("00"):
        market = "SZ"
    else:
        return None
    if "ST" in name.upper():
        return None
    return {"code": code, "name": name, "market": market, "board": "主板", "is_st": False}


async def _candidate_profiles(
    trade_date: date, repos: Repositories, *, write_stocks: bool = True
) -> list[dict[str, Any]]:
    """推导「策略相关票」：近 :data:`DAILY_BAR_LOOKBACK_DAYS` 个自然日**已入库**的
    涨停池与连板天梯（主板 + 非 ST），按代码升序去重。

    ``write_stocks=True`` 时顺带把票的基础信息补写进 ``stocks``（daily_bars 路径）。
    """
    start = trade_date - timedelta(days=DAILY_BAR_LOOKBACK_DAYS)
    candidates: dict[str, str] = {}
    for code, name in await repos.limit_up_pool.codes_between(start, trade_date):
        candidates[code] = name
    for row in await repos.ladder.get_range(start, trade_date):
        candidates.setdefault(row.code, row.name)

    profiles = [
        profile
        for code, name in candidates.items()
        if (profile := _main_board_profile(code, name)) is not None
    ]
    profiles.sort(key=lambda item: str(item["code"]))
    if profiles and write_stocks:
        await repos.stocks.upsert_many(profiles)
    return profiles


async def _daily_bar_args(trade_date: date, repos: Repositories) -> list[dict[str, Any]]:
    """日线取数参数：**每只策略相关票一份**（上游按单只证券取历史区间）。

    「策略相关票」由近 :data:`DAILY_BAR_LOOKBACK_DAYS` 个自然日**已入库**的涨停池
    与连板天梯推导（主板 + 非 ST），并顺带把票的基础信息补写进 ``stocks`` 供策略读取；
    库中暂无相关票时返回空列表（成功 0 行），不新增数据源、不拉全市场。
    """
    profiles = await _candidate_profiles(trade_date, repos)
    start = trade_date - timedelta(days=DAILY_BAR_LOOKBACK_DAYS)
    start_ms, end_ms = date_ms(start), day_end_ms(trade_date)
    return [
        {
            "thscode": profile["code"],
            "start_ms": start_ms,
            "end_ms": end_ms,
            "adjust": "none",
        }
        for profile in profiles
    ]


async def _minute_bar_args(trade_date: date, repos: Repositories) -> list[dict[str, Any]]:
    """分时取数参数：每只策略相关票一份（eltdx 按单只证券取当日/历史分时）。"""
    profiles = await _candidate_profiles(trade_date, repos, write_stocks=False)
    return [{"thscode": profile["code"], "date": trade_date.isoformat()} for profile in profiles]


_REGISTRY: dict[str, IngestTaskDef] = {}


def register_task(defn: IngestTaskDef) -> IngestTaskDef:
    """注册任务定义。

    Raises:
        TaskRegistryError: 任务名重复，或引用了未登记的写入器。
    """
    if defn.name in _REGISTRY:
        raise TaskRegistryError(f"任务名重复: {defn.name!r}")
    if defn.target not in WRITERS:
        raise TaskRegistryError(
            f"任务 {defn.name!r} 引用未登记写入器 {defn.target!r}（已登记：{sorted(WRITERS)}）"
        )
    _REGISTRY[defn.name] = defn
    return defn


def all_tasks() -> list[IngestTaskDef]:
    """返回全部已注册任务（**按注册顺序**，即 :data:`DEFAULT_TASKS` 声明顺序）。

    声明顺序即执行依赖顺序：``daily_bars`` 的标的由「近期涨停池 + 连板天梯」推导，
    故声明在 ``ladder`` 之后——同窗口冷启动时先入库池/天梯再采日线。
    """
    return list(_REGISTRY.values())


def get_task(name: str) -> IngestTaskDef:
    """按名取任务定义。

    Raises:
        KeyError: 任务未注册。
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"未注册的采集任务: {name!r}（已注册：{sorted(_REGISTRY)}）") from None


def reset_tasks() -> None:
    """清空注册表（仅供测试隔离使用）。"""
    _REGISTRY.clear()


#: 默认任务集：覆盖 Phase-1 全部能力。
DEFAULT_TASKS: tuple[IngestTaskDef, ...] = (
    IngestTaskDef(
        name="trading_calendar",
        capability="trading_calendar",
        target="trading_calendar",
        window=None,
        interval_seconds=0,
        idempotency_key=_default_key,
        args_builder=_date_args,
    ),
    IngestTaskDef(
        name="limit_up_pool",
        capability="limit_up_pool",
        # 交易时段轮询 + 整批替换：09:25 首封起每 10 分钟拉一次（午休 11:30-13:00 跳过），
        # 15:05 收口（收盘后仍有最后一刀）。库中只保留**最近一次**快照——
        # 「先涨停、后炸板」的票不会残留（replace 语义，非 upsert 并集）。
        # 下游读取一律走库（/api/pools），不直连上游。
        target="limit_up_pool_replace",
        window=Window("trading_hours", "09:25", "15:05", breaks=(("11:30", "13:00"),)),
        interval_seconds=600,
        idempotency_key=_default_key,
        args_builder=_limit_up_pool_args,
    ),
    IngestTaskDef(
        name="limit_up_pool_supplement",
        capability="limit_up_pool_supplement",
        # 第 8 轮补数：主源 xuangutong 不提供封单金额/最大封单金额，本任务固定走
        # hithink（主备链仅 hithink），把 seal_amount_yuan / max_seal_amount_yuan /
        # limit_up_time **合并回填**到当日 limit_up 池行——不插入新行、不覆盖主源
        # 的涨停原因/时间线等列。与主任务同窗口同节奏，每轮紧跟其后执行。
        target="limit_up_pool_supplement",
        window=Window("trading_hours", "09:25", "15:05", breaks=(("11:30", "13:00"),)),
        interval_seconds=600,
        idempotency_key=_default_key,
        args_builder=_limit_up_supplement_args,
    ),
    IngestTaskDef(
        name="newsflash",
        capability="newsflash",
        target="newsflash",
        # 全天采集（对齐旧 quant 的 30s 广播 / 60s 上游有效间隔）；
        # 上游只回最近 ~50 条，按 (ts, title) 幂等增量入库，保留 7 天。
        window=None,
        interval_seconds=60,
        idempotency_key=_default_key,
        args_builder=_date_args,
    ),
    IngestTaskDef(
        name="theme_rank",
        capability="theme_rank",
        target="theme_rank_replace",
        # 交易时段每 30 分钟一轮（主题榜变化慢于个股，半小时足够）；
        # **整批替换**当日题材行——榜单滚动变化，合并写入会残留已掉出的题材名。
        window=Window("trading_hours", "09:25", "15:05", breaks=(("11:30", "13:00"),)),
        interval_seconds=1800,
        idempotency_key=_default_key,
        args_builder=_date_args,
    ),
    IngestTaskDef(
        name="theme_stocks",
        capability="theme_stocks",
        target="theme_stocks_replace",
        # 与题材榜同窗口同节拍，保证「榜单 + 成分」始终同一时刻口径；
        # 整批替换当日成分股，并按题材聚合回填 `themes.core_count`。
        window=Window("trading_hours", "09:25", "15:05", breaks=(("11:30", "13:00"),)),
        interval_seconds=1800,
        idempotency_key=_default_key,
        args_builder=_date_args,
    ),
    IngestTaskDef(
        name="ladder",
        capability="ladder",
        target="ladder",
        # 交易时段每 10 分钟一轮：上游一次返回近 30 个交易日的完整矩阵，
        # 重跑按 (trade_date, code) 幂等覆盖，盘中可持续刷新当日连板层。
        window=Window("trading_hours", "09:25", "15:05", breaks=(("11:30", "13:00"),)),
        interval_seconds=600,
        idempotency_key=_default_key,
        args_builder=_date_args,
    ),
    IngestTaskDef(
        name="daily_bars",
        capability="daily_bars",
        target="daily_bars",
        window=Window("postmarket", "17:00", "18:00"),
        interval_seconds=0,
        idempotency_key=_default_key,
        # 注意：声明在 ladder 之后——标的推导依赖近期涨停池/天梯已入库（见 all_tasks）。
        args_builder=_daily_bar_args,
    ),
    IngestTaskDef(
        name="monitor_stocks",
        capability="monitor_stocks",
        target="monitor_stocks",
        # 监管名单是**日度参考名单**：交易所的重点监控/异动公告多在收盘后发布，盘中
        # 抓到的是前一日口径，故放**盘后窗口**（17:00-18:00）每 30 分钟一轮（2-3 轮），
        # 同一天多轮幂等覆盖，取当日最后一次快照。
        # 不挂 09:25-15:05 高频窗口：该页面不是盘中数据，高频拉取只会白耗上游。
        window=Window("postmarket", "17:00", "18:00"),
        interval_seconds=1800,
        idempotency_key=_default_key,
        args_builder=_monitor_stocks_args,
    ),
    IngestTaskDef(
        name="monitor_unusual",
        capability="monitor_unusual",
        target="monitor_stocks",
        # 与重点监控同窗口同节拍（同一业务域、同一张表、同一写入器），
        # 但**独立任务**：能力不同（响应形状不同），且 severe 是多页取数。
        window=Window("postmarket", "17:00", "18:00"),
        interval_seconds=1800,
        idempotency_key=_default_key,
        args_builder=_monitor_unusual_args,
    ),
    IngestTaskDef(
        name="market_sentiment",
        capability="market_sentiment",
        target="market_sentiment_cycle",
        # 交易时段每 10 分钟一轮：上游返回当日分钟级情绪序列，取最新点入当日行。
        window=Window("trading_hours", "09:25", "15:05", breaks=(("11:30", "13:00"),)),
        interval_seconds=600,
        idempotency_key=_default_key,
        args_builder=_date_args,
    ),
    IngestTaskDef(
        name="minute_bars",
        capability="minute_bars",
        target="minute_bars",
        # 分时：盘中 5 分钟一轮（覆盖 09:30-15:00，含午休空档，幂等覆盖写无害）；
        # 标的为策略相关票（近 30 天涨停池/天梯推导），供分时形态因子与前端分时图。
        window=Window("intraday_day", "09:30", "15:00"),
        interval_seconds=300,
        idempotency_key=_default_key,
        args_builder=_minute_bar_args,
    ),
    IngestTaskDef(
        name="opening_match",
        capability="opening_match",
        target="opening_match",
        # 09:25 正式撮合（一次性）：当日开盘价，供策略 Phase.OPENING 盘中判定。
        window=Window("auction", "09:25", "09:40"),
        interval_seconds=0,
        idempotency_key=_default_key,
        args_builder=_minute_bar_args,
    ),
    IngestTaskDef(
        name="auction_series",
        capability="auction_series",
        target="auction_series",
        # 竞价时序（一次性）：9:15~9:25 逐点虚拟撮合，writer 提炼 9:20 参考价，
        # 供 J1 竞价抢筹三腿判定；窗口同 opening_match（09:25 后时序已定版）。
        window=Window("auction", "09:25", "09:40"),
        interval_seconds=0,
        idempotency_key=_default_key,
        args_builder=_minute_bar_args,
    ),
)


def _register_defaults() -> None:
    """导入本模块即注册默认任务集（幂等）。"""
    for defn in DEFAULT_TASKS:
        if defn.name not in _REGISTRY:
            register_task(defn)


_register_defaults()
