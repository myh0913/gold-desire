"""采集任务注册表（声明式）。

每个**能力**（capability）注册一个 :class:`IngestTaskDef`，声明：

- ``capability``：数据源能力名（见 ``app.datasources.contracts``）；
- ``target``：写入器键（:data:`WRITERS`），**唯一**决定「契约 → 目标表列」的映射，
  集中在各能力自己的 writer 函数里，消费方零改动；
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

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from app.core.timeutil import date_ms, day_end_ms
from app.datasources.contracts import ContractModel
from app.ingest.windows import Window
from app.repositories import Repositories

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TASKS",
    "WRITERS",
    "IngestTaskDef",
    "TaskRegistryError",
    "all_tasks",
    "get_task",
    "register_task",
    "reset_tasks",
]

#: 写入器：把契约对象列表落库，返回写入行数。
WriterFn = Callable[[Repositories, Sequence[ContractModel], date, str], Awaitable[int]]

#: 取数参数构造器：``(trade_date, repos) -> 一组参数``（每份对应一轮取数）。
ArgsBuilder = Callable[[date, Repositories], Awaitable[Sequence[dict[str, Any]]]]

#: 交易日历在 ``pool_snapshot`` 中的池名（缓存交易日集合，见 scheduler）。
CALENDAR_POOL_NAME = "trading_calendar"


class TaskRegistryError(RuntimeError):
    """任务注册表不一致（重名 / 写入器缺失）。"""


# ============================================================ 各能力写入器
# 每能力的「契约字段 → 目标表列」映射只在此处声明一次。


def _dump(row: ContractModel, source: str) -> dict[str, Any]:
    """契约对象 → 表行字典（附来源列）。"""
    return {**row.model_dump(), "source": source}


async def _write_daily_bars(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """日线：契约字段与 ``daily_bars`` 列同名，按 ``(code, trade_date)`` 幂等。"""
    return await repos.daily_bars.upsert_many([_dump(row, source) for row in rows])


async def _write_limit_up_pool(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """涨停池：契约无 ``trade_date``，由任务参数补入，按 ``(trade_date,pool_type,code)`` 幂等。"""
    payload = [{**_dump(row, source), "trade_date": trade_date} for row in rows]
    return await repos.limit_up_pool.upsert_many(payload)


async def _replace_limit_up_pool(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """涨停池（盘中轮询）：**整批替换**当日快照，库中只保留最近一次拉取结果。

    与 :func:`_write_limit_up_pool` 的区别只在写入语义：本写入器先删除当日该
    ``pool_type`` 的旧行再写入，因此「本轮已掉出池子」的票不会残留（例如先涨停
    后炸板）。空结果不触发删除，见 ``LimitUpPoolRepository.replace_pool``。
    """
    payload = [{**_dump(row, source), "trade_date": trade_date} for row in rows]
    return await repos.limit_up_pool.replace_pool(trade_date, payload)


async def _write_ladder(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """连板天梯：按 ``(trade_date, code)`` 幂等。"""
    return await repos.ladder.upsert_many([_dump(row, source) for row in rows])


async def _write_market_sentiment(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """市场情绪：按 ``trade_date`` 幂等。"""
    return await repos.market_sentiment.upsert_many([_dump(row, source) for row in rows])


async def _replace_theme_rank(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """题材榜（盘中轮询）：**整批替换**当日题材行。

    榜单是完整快照，盘中会滚动变化；``upsert_many`` 只覆盖不删除，会让早先入榜、
    之后掉出的题材名永久残留（实测当日累积到 44 行 / rank 到 42，而当前榜单只有
    24 个）。空结果不触发删除，见 ``ThemeRepository.replace_themes``。
    """
    themes = [_dump(row, source) for row in rows]
    return await repos.themes.replace_themes(trade_date, themes=themes)


async def _replace_theme_stocks(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """题材成分股（盘中轮询）：**整批替换**当日成分股，并回填题材核心股数。

    ``core_count`` 上游不提供（``plate/data`` 该字段恒为 ``null``），由本轮成分股
    聚合得到——按题材名统计后回填当日题材行。
    """
    stocks = [_dump(row, source) for row in rows]
    total = await repos.themes.replace_themes(trade_date, stocks=stocks)
    counts: dict[str, int] = {}
    for stock in stocks:
        name = stock.get("theme_name")
        if isinstance(name, str) and name:
            counts[name] = counts.get(name, 0) + 1
    if counts:
        await repos.themes.set_core_counts(trade_date, counts)
    return total


async def _write_market_sentiment_with_cycle(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """情绪指标：写入后**随即判定情绪周期并落库**。

    周期（六态 / 依据 / 指标 / 过热 / 仓位因子）是情绪指标 + 涨停池的**派生**结果，
    与情绪指标同窗口同节拍，保证总览页看到的两者同一时刻口径。参考实现是让独立
    pipeline 每轮算一次并落报告文件；这里落在同一步，避免多一个调度任务。

    判定失败**不阻断**情绪写入（本任务主职责是落情绪指标），但会记完整堆栈——
    周期缺失时总览显示「暂无周期数据」，不会静默糊过去。
    """
    from app.services.cycle import CycleService

    written = await _write_market_sentiment(repos, rows, trade_date, source)
    try:
        judgement = await CycleService(repos).judge(trade_date)
    except Exception:
        logger.exception(
            "cycle_judge_failed",
            extra={"trade_date": trade_date.isoformat(), "task": "market_sentiment"},
        )
    else:
        logger.info(
            "cycle_judged",
            extra={
                "trade_date": trade_date.isoformat(),
                "state": judgement.state.value,
                "overheated": judgement.overheated,
                "data_degraded": judgement.data_degraded,
            },
        )
    return written


async def _write_newsflash(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """快讯：按 ``(ts, title)`` 幂等。"""
    return await repos.news_flash.upsert_many([_dump(row, source) for row in rows])


async def _write_minute_bars(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """分时分钟点：按 ``(code, trade_date, minute_index)`` 幂等。"""
    return await repos.minute_bars.upsert_many([_dump(row, source) for row in rows])


#: opening_match 写入 ``pool_snapshot`` 的池名（幂等键 ``(trade_date, pool_name)``）。
OPENING_MATCH_POOL_NAME = "opening_match"


async def _write_opening_match(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """09:25 撮合：**复用** ``pool_snapshot`` 存储（``pool_name='opening_match'``）。

    ``payload`` 为 ``{code: {price, volume_lots, time_label}}`` 映射，供策略
    盘中阶段（Phase.OPENING）读取当日开盘价；无撮合数据的票不出现在映射中。
    多轮取数（逐票）按**合并**语义写入——后一轮只增改自己的键，不清空前轮
    （行级 ``upsert`` 以 ``(trade_date, pool_name)`` 为冲突键，整包覆盖会丢前轮）。
    """
    existing = await repos.pool_snapshot.get(trade_date, OPENING_MATCH_POOL_NAME)
    merged: dict[str, Any] = dict(existing.payload) if existing is not None else {}
    for row in rows:
        merged[row.code] = {
            "price": row.price,
            "volume_lots": row.volume_lots,
            "time_label": row.time_label,
        }
    return await repos.pool_snapshot.upsert_many(
        [
            {
                "trade_date": trade_date,
                "pool_name": OPENING_MATCH_POOL_NAME,
                "payload": merged,
                "source": source,
            }
        ]
    )


async def _write_trading_calendar(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """交易日历：**复用** ``pool_snapshot`` 存储（``pool_name='trading_calendar'``）。

    本 Task 不新增 ORM 模型，故以池快照承载日历缓存：``payload`` 含开市日期列表、
    逐日开关映射与抓取时间（供 TTL 判定），按 ``(trade_date, pool_name)`` 幂等覆盖。
    """
    days = [row.model_dump() for row in rows]
    payload: dict[str, Any] = {
        "dates": [d["trade_date"].isoformat() for d in days if d.get("is_open")],
        "is_open": {d["trade_date"].isoformat(): bool(d.get("is_open")) for d in days},
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    return await repos.pool_snapshot.upsert_many(
        [
            {
                "trade_date": trade_date,
                "pool_name": CALENDAR_POOL_NAME,
                "payload": payload,
                "source": source,
            }
        ]
    )


#: 能力/写入器键 → 写入函数。新增能力只需在此登记一个 writer。
WRITERS: dict[str, WriterFn] = {
    "daily_bars": _write_daily_bars,
    "limit_up_pool": _write_limit_up_pool,
    "limit_up_pool_replace": _replace_limit_up_pool,
    "ladder": _write_ladder,
    "market_sentiment": _write_market_sentiment,
    "market_sentiment_cycle": _write_market_sentiment_with_cycle,
    "theme_rank_replace": _replace_theme_rank,
    "theme_stocks_replace": _replace_theme_stocks,
    "newsflash": _write_newsflash,
    "trading_calendar": _write_trading_calendar,
    "minute_bars": _write_minute_bars,
    "opening_match": _write_opening_match,
}


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
)


def _register_defaults() -> None:
    """导入本模块即注册默认任务集（幂等）。"""
    for defn in DEFAULT_TASKS:
        if defn.name not in _REGISTRY:
            register_task(defn)


_register_defaults()
