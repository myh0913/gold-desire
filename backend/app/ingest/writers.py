"""采集写入器：各能力「契约字段 → 目标表列」映射（声明式）。

每个**能力**（capability）在 :data:`WRITERS` 登记唯一一个写入器，负责把
``provider.fetch`` 返回的契约对象列表落库到该能力的目标表。契约 → 列的映射
只在此处声明一次，消费方零改动；任务编排与注册表见 :mod:`app.ingest.tasks`。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, cast

from app.datasources.contracts import (
    AuctionSeriesContract,
    ContractModel,
    OpeningMatchContract,
)
from app.repositories import Repositories

logger = logging.getLogger(__name__)

__all__ = [
    "AUCTION_SERIES_POOL_NAME",
    "CALENDAR_POOL_NAME",
    "OPENING_MATCH_POOL_NAME",
    "WRITERS",
    "WriterFn",
]

#: 写入器：把契约对象列表落库，返回写入行数。
WriterFn = Callable[[Repositories, Sequence[ContractModel], date, str], Awaitable[int]]

# ============================================================ 各能力写入器
# 每能力的「契约字段 → 目标表列」映射只在此处声明一次。


def _dump(row: ContractModel, source: str) -> dict[str, Any]:
    """契约对象 → 表行字典（附来源列）。"""
    return {**row.model_dump(), "source": source}


#: 契约中不属于 ``limit_up_pool`` 表的列（``list_date`` 属于 ``stocks`` 表，
#: 由写入器剥离后单独喂给 :meth:`StockRepository.backfill_list_date`）。
_NON_POOL_COLUMNS = frozenset({"list_date"})


def _strip_non_pool_columns(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """剥离池表没有的契约列，避免 bulk_upsert 报 Unconsumed column names。"""
    return [
        {key: value for key, value in row.items() if key not in _NON_POOL_COLUMNS}
        for row in rows
    ]


async def _write_daily_bars(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """日线：契约字段与 ``daily_bars`` 列同名，按 ``(code, trade_date)`` 幂等。"""
    return await repos.daily_bars.upsert_many([_dump(row, source) for row in rows])


async def _write_limit_up_pool(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """涨停池：契约无 ``trade_date``，由任务参数补入，按 ``(trade_date,pool_type,code)`` 幂等。

    契约的 ``list_date``（xuangutong ``listed_date``）不属于池表——剥离后喂给
    ``stocks.backfill_list_date``（只补 ``stocks.list_date`` 的 NULL 行）。
    """
    payload = [{**_dump(row, source), "trade_date": trade_date} for row in rows]
    await repos.stocks.backfill_list_date(payload)
    return await repos.limit_up_pool.upsert_many(_strip_non_pool_columns(payload))


async def _replace_limit_up_pool(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """涨停池（盘中轮询）：**整批替换**当日快照，库中只保留最近一次拉取结果。

    与 :func:`_write_limit_up_pool` 的区别只在写入语义：本写入器先删除当日该
    ``pool_type`` 的旧行再写入，因此「本轮已掉出池子」的票不会残留（例如先涨停
    后炸板）。空结果不触发删除，见 ``LimitUpPoolRepository.replace_pool``。

    顺带把 xuangutong 池返回的 ``listed_date`` 回填到 ``stocks.list_date``
    （只补 NULL 行，不覆盖既有值；``stocks`` 行由 daily_bars 路径创建）。
    """
    payload = [{**_dump(row, source), "trade_date": trade_date} for row in rows]
    await repos.stocks.backfill_list_date(payload)
    return await repos.limit_up_pool.replace_pool(trade_date, _strip_non_pool_columns(payload))


#: 涨停池补数轮只回填 hithink 独有的这几列（不覆盖主源 xuangutong 的其他字段）。
_LIMIT_UP_SUPPLEMENT_COLUMNS = ("seal_amount_yuan", "max_seal_amount_yuan", "limit_up_time")


async def _merge_limit_up_supplement(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """涨停池补数（第 8 轮）：**合并回填**当日 limit_up 池行的封单金额等列。

    xuangutong 主源不提供封单金额/最大封单金额，hithink 端点有——本写入器按
    ``(trade_date, pool_type, code)`` 命中既有行后只回填
    :data:`_LIMIT_UP_SUPPLEMENT_COLUMNS`，不插入新行、不覆盖主源的涨停原因/
    时间线/量比等列。未命中的行忽略（两源池口径略有出入属正常）。
    """
    payload = [{**_dump(row, source), "trade_date": trade_date} for row in rows]
    return await repos.limit_up_pool.merge_supplement(
        trade_date, payload, _LIMIT_UP_SUPPLEMENT_COLUMNS
    )


async def _write_ladder(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """连板天梯：按 ``(trade_date, code)`` 幂等。

    写入后从**已入库的涨停池**按 code 回填 ``first_seal_time``——hithink ladder
    端点不返回首次封板时间（该字段在 ladder 映射中为死映射），涨停池的
    ``limit_up_time`` 同日同票可用（对齐 quant 前端「封板时间」取池数据的口径）。
    一次拉取覆盖约 30 个交易日，对本轮涉及的全部日期逐日回填（幂等，只补 NULL）。
    """
    written = await repos.ladder.upsert_many([_dump(row, source) for row in rows])
    dates = sorted(
        {
            day
            for row in rows
            if (day := getattr(row, "trade_date", None)) is not None
        }
    )
    for day in dates:
        await repos.ladder.backfill_first_seal_from_pool(day)
    return written


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

    周期态**变化时经 WS ``cycle`` 频道推送**（T-0004）：盘中 trading_hours 每 10 分钟
    判一次，状态一旦变化立即通知前端与策略门控消费方（「恶化立即生效」——门控读
    ``cycle_judgements`` 当日行，无需额外接线）。推送失败不影响判定结果。
    """
    from app.core.ws_bus import publish_event
    from app.services.cycle import CycleService, _position_factor

    written = await _write_market_sentiment(repos, rows, trade_date, source)
    previous = await repos.cycle_judgements.get(trade_date)
    prev_state = str(getattr(previous, "state", "") or "") if previous is not None else ""
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
        if judgement.state.value != prev_state:
            try:
                await publish_event(
                    "cycle",
                    {
                        "source": "cycle:judge",
                        "trade_date": trade_date.isoformat(),
                        "previous_state": prev_state or None,
                        "state": judgement.state.value,
                        "position_factor": _position_factor(judgement.state),
                        "overheated": judgement.overheated,
                        "reasons": list(judgement.reasons),
                        "at": datetime.now(UTC).isoformat(),
                    },
                )
            except Exception:  # pragma: no cover - 推送失败不影响判定
                logger.warning(
                    "cycle_state_broadcast_failed",
                    extra={"trade_date": trade_date.isoformat()},
                )
    return written


async def _write_newsflash(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """快讯：按 ``(ts, title)`` 幂等。"""
    return await repos.news_flash.upsert_many([_dump(row, source) for row in rows])


def _newer_notice(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """候选行的公告日是否比现有行更晚（缺失一律视为「不比现有新」）。"""
    new_date = candidate.get("notice_date")
    old_date = current.get("notice_date")
    if new_date is None:
        return False
    if old_date is None:
        return True
    return bool(new_date > old_date)


def _dedupe_monitor_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """按 ``(kind, code)`` 去重，保留**公告日最新**的一条（保序：首次出现的位置）。

    为什么必须去重：``monitor_stocks`` 的唯一键是 ``(trade_date, kind, code)``，而东财
    异常波动端点**一只证券可以有多条公告**（实测一页 50 条里有 4 个重复代码）。同一批
    里出现重复冲突键时，PostgreSQL 的 ``ON CONFLICT DO UPDATE`` 会直接报
    ``cannot affect row a second time``——整轮采集失败。故必须先去重再 upsert。

    为什么只留一条：表结构本身即「每票每日一条」（见唯一键），一条公告已能回答
    「这只票为什么进名单」；更早的公告由上游分页提供，不必重复落库。
    """
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        key = (str(item.get("kind") or ""), str(item.get("code") or ""))
        current = best.get(key)
        if current is None or _newer_notice(item, current):
            best[key] = item
    return list(best.values())


async def _write_monitor_stocks(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """监管名单：按 ``(trade_date, kind, code)`` 幂等写入（**批内先去重**，见上）。"""
    payload = _dedupe_monitor_rows([_dump(row, source) for row in rows])
    return await repos.monitor_stocks.upsert_many(payload)


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
    for raw in rows:
        row = cast(OpeningMatchContract, raw)
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


#: auction_series 写入 ``pool_snapshot`` 的池名（幂等键 ``(trade_date, pool_name)``）。
AUCTION_SERIES_POOL_NAME = "auction_series"


async def _write_auction_series(
    repos: Repositories, rows: Sequence[ContractModel], trade_date: date, source: str
) -> int:
    """竞价时序：**复用** ``pool_snapshot`` 存储（``pool_name='auction_series'``）。

    单轮取数为**单票的整段时序**（9:15~9:25 逐点），写入前就地提炼 9:20 参考
    价——取**第一个 ``time_label`` 以 ``"09:20"`` 开头**的点（口径对齐情绪周期
    研究 ``verify_auction_pilot.py`` 的 p920）；无该点的票不写入映射。
    ``payload`` 为 ``{code: {p920, time_label}}``，供 J1 竞价抢筹盘中判定。
    多轮取数（逐票）按**合并**语义写入（同 :func:`_write_opening_match`）。
    """
    existing = await repos.pool_snapshot.get(trade_date, AUCTION_SERIES_POOL_NAME)
    merged: dict[str, Any] = dict(existing.payload) if existing is not None else {}
    by_code: dict[str, list[AuctionSeriesContract]] = {}
    for row in rows:
        if isinstance(row, AuctionSeriesContract):
            by_code.setdefault(row.code, []).append(row)
    for code, points in by_code.items():
        ref = next((p for p in points if p.time_label.startswith("09:20")), None)
        if ref is not None:
            merged[code] = {"p920": ref.price, "time_label": ref.time_label}
    return await repos.pool_snapshot.upsert_many(
        [
            {
                "trade_date": trade_date,
                "pool_name": AUCTION_SERIES_POOL_NAME,
                "payload": merged,
                "source": source,
            }
        ]
    )


#: 交易日历能力对应的池名（写入 ``pool_snapshot``）。
CALENDAR_POOL_NAME = "trading_calendar"


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
    "limit_up_pool_supplement": _merge_limit_up_supplement,
    "ladder": _write_ladder,
    "market_sentiment": _write_market_sentiment,
    "market_sentiment_cycle": _write_market_sentiment_with_cycle,
    "theme_rank_replace": _replace_theme_rank,
    "theme_stocks_replace": _replace_theme_stocks,
    "newsflash": _write_newsflash,
    "monitor_stocks": _write_monitor_stocks,
    "trading_calendar": _write_trading_calendar,
    "minute_bars": _write_minute_bars,
    "opening_match": _write_opening_match,
    "auction_series": _write_auction_series,
}
