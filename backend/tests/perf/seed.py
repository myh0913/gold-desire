"""性能测试种子：构造一个足以让读路径「真实干活」的市场数据集。

规模目标（spec 要求：让读接口的 DB 工作量接近真实负载）：

- ~2000 条日线（100 个股票 × 20 个交易日）；
- ~20 个股票基础信息；
- ~50 条情绪历史；
- ~200 条涨停池行（跨多日）；
- ~300 条天梯行；
- ~30 条建议报告；
- 若干主题 / 快讯 / 监控（覆盖对应读接口即可）。

实现要点：

- 复用 :mod:`tests.conftest` 的字段构造器（``_bar`` / ``_pool_row`` / ``_sentiment_row``），
  不复制 magic 值；
- 全部走各仓储的 ``upsert_many`` / ``create_draft`` / ``create`` 等幂等 API，
  重复调用不会触发主键冲突；
- 配套两个常量（``TRADE_DATE_START`` / ``TRADE_DATE_END``）让压测用例按真实日期窗口
  命中 ``/api/bars/daily`` / ``/api/ladder``。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.repositories import Repositories
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

#: 起始交易日：所有种子的「最早一天」。
TRADE_DATE_START: date = date(2026, 1, 5)
#: 结束交易日：种子数据最晚一天（与现有 conftest 共用 ``TRADE_DATE``）。
TRADE_DATE_END: date = date(2026, 6, 30)

NUM_STOCKS = 20
NUM_BARS_PER_STOCK = 100  # 20 × 100 = 2000 条日线
NUM_SENTIMENT_ROWS = 50
NUM_POOL_ROWS = 200
NUM_LADDER_ROWS = 300
NUM_ADVICE_ROWS = 30


def _trade_dates(start: date, end: date, count: int) -> list[date]:
    """生成 ``count`` 个从 ``start`` 到 ``end`` 等距分布的日期（升序）。"""
    if count <= 1:
        return [start]
    span_days = (end - start).days
    if span_days <= 0:
        return [start for _ in range(count)]
    step = span_days // (count - 1)
    return [start + timedelta(days=step * index) for index in range(count)]


def _bar(
    code: str,
    day: date,
    pre_close: str,
    o: str,
    h: str,
    low: str,
    close: str,
    volume: int = 1_000_000,
) -> dict[str, Any]:
    """构造一条日线（与 ``tests/conftest.py`` 字段口径一致）。"""
    return {
        "code": code,
        "trade_date": day,
        "open": Decimal(o),
        "high": Decimal(h),
        "low": Decimal(low),
        "close": Decimal(close),
        "pre_close": Decimal(pre_close),
        "volume_shares": volume,
        "amount_yuan": Decimal("1000000.00"),
        "source": "fake",
    }


def _stock_rows(num_stocks: int = NUM_STOCKS) -> list[dict[str, Any]]:
    """构造股票基础信息行（代码从 ``600000`` 起递增）。"""
    rows: list[dict[str, Any]] = []
    for index in range(num_stocks):
        code = f"{600000 + index:06d}"
        rows.append(
            {
                "code": code,
                "name": f"测试股{index:02d}",
                "market": "SH" if index % 2 == 0 else "SZ",
                "board": "主板",
                "is_st": False,
                "list_date": date(2015, 1, 1),
                "source": "fake",
            }
        )
    return rows


def _daily_bar_rows(dates: Sequence[date]) -> list[dict[str, Any]]:
    """为每个股票生成区间内全部日期的日线（无意义但连续的 OHLC）。"""
    rows: list[dict[str, Any]] = []
    for stock_index in range(NUM_STOCKS):
        code = f"{600000 + stock_index:06d}"
        base_close = Decimal("10.00") + Decimal(stock_index) * Decimal("0.50")
        for offset, day in enumerate(dates):
            pre_close = base_close + Decimal(offset) * Decimal("0.05")
            open_px = pre_close + Decimal("0.10")
            high_px = open_px + Decimal("0.50")
            low_px = open_px - Decimal("0.40")
            close_px = open_px + Decimal("0.20")
            rows.append(
                _bar(
                    code,
                    day,
                    pre_close=str(pre_close),
                    o=str(open_px),
                    h=str(high_px),
                    low=str(low_px),
                    close=str(close_px),
                    volume=1_000_000 + stock_index * 1000 + offset * 100,
                )
            )
    return rows


def _pool_rows(dates: Sequence[date]) -> list[dict[str, Any]]:
    """构造跨多日的涨停池行。"""
    pool_types = ("limit_up", "broken", "strong", "prev_limit_up")
    rows: list[dict[str, Any]] = []
    for day_index, day in enumerate(dates):
        # 每天 5 行 → 50 天 × 4 行 ≈ 200 行
        for slot in range(5):
            stock_index = (day_index * 5 + slot) % NUM_STOCKS
            code = f"{600000 + stock_index:06d}"
            pool_type = pool_types[(day_index + slot) % len(pool_types)]
            rows.append(
                {
                    "trade_date": day,
                    "code": code,
                    "name": f"测试股{stock_index:02d}",
                    "continue_days": 1 + ((day_index + slot) % 5),
                    "limit_up_time": f"09:{31 + (slot % 20):02d}",
                    "seal_amount_yuan": Decimal("1000000.00"),
                    "open_times": slot % 3,
                    "turnover_rate": Decimal("0.0812"),
                    "amount_yuan": Decimal("2000000.00"),
                    "market_cap_yuan": Decimal("3000000000.00"),
                    "pool_type": pool_type,
                    "source": "fake",
                }
            )
    return rows


def _ladder_rows(dates: Sequence[date]) -> list[dict[str, Any]]:
    """构造天梯行（每天 ~6 条）。"""
    rows: list[dict[str, Any]] = []
    for day_index, day in enumerate(dates):
        for slot in range(6):
            stock_index = (day_index * 6 + slot) % NUM_STOCKS
            code = f"{600000 + stock_index:06d}"
            rows.append(
                {
                    "trade_date": day,
                    "code": code,
                    "name": f"测试股{stock_index:02d}",
                    "continue_days": 2 + ((day_index + slot) % 5),
                    "first_seal_time": f"09:{30 + (slot % 25):02d}",
                    "source": "fake",
                }
            )
    return rows


def _sentiment_rows(count: int = NUM_SENTIMENT_ROWS) -> list[dict[str, Any]]:
    """构造情绪历史（升序）。"""
    dates = _trade_dates(TRADE_DATE_START, TRADE_DATE_END, count)
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(dates):
        rows.append(
            {
                "trade_date": day,
                "temperature": Decimal("55.0000") + Decimal(index) * Decimal("0.30"),
                "stage": "加速/高潮",
                "limit_up_count": 42 + (index % 10),
                "limit_down_count": 3 + (index % 4),
                "broken_board_count": 6,
                "broken_rate": Decimal("0.1250"),
                "up_count": 2600,
                "down_count": 1400,
                "max_continue_days": 5,
                "premium_rate": Decimal("0.0250"),
                "source": "fake",
            }
        )
    return rows


def _advice_rows(count: int = NUM_ADVICE_ROWS) -> list[dict[str, Any]]:
    """构造建议报告（覆盖多日 + 多策略）。"""
    dates = _trade_dates(TRADE_DATE_START, TRADE_DATE_END, count)
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(dates):
        strategy_id = "dragon" if index % 2 == 0 else "tailpan"
        rows.append(
            {
                "trade_date": day,
                "kind": "advice",
                "strategy_id": strategy_id,
                "strategy_version": 1,
                "payload": {
                    "path_id": "S2",
                    "code": f"{600000 + (index % NUM_STOCKS):06d}",
                    "position": 0.2,
                },
                "ran_at": datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(
                    hour=15, minute=5
                ),
            }
        )
    return rows


def _newsflash_rows() -> list[dict[str, Any]]:
    """少量快讯（仅覆盖 ``/api/newsflash``）。"""
    return [
        {
            "ts": datetime(2026, 6, 3, 9, 0, tzinfo=UTC),
            "title": "测试快讯一",
            "summary": "摘要一",
            "symbols": ["600000"],
            "categories": ["宏观"],
            "source": "fake",
        },
        {
            "ts": datetime(2026, 6, 3, 10, 0, tzinfo=UTC),
            "title": "测试快讯二",
            "summary": None,
            "symbols": [],
            "categories": [],
            "source": "fake",
        },
    ]


async def seed_perf_dataset(factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """种入性能测试所需数据集（幂等；重复执行不抛错）。

    Returns:
        dict 含 ``start`` / ``end`` / ``stocks`` 等关键标识，供压测用例按真实日期窗口命中。
    """
    dates = _trade_dates(TRADE_DATE_START, TRADE_DATE_END, NUM_BARS_PER_STOCK)

    async with factory() as session:
        repos = Repositories.build(session)

        await repos.stocks.upsert_many(_stock_rows())
        await repos.daily_bars.upsert_many(_daily_bar_rows(dates))
        await repos.market_sentiment.upsert_many(_sentiment_rows())
        await repos.limit_up_pool.upsert_many(_pool_rows(dates))
        await repos.ladder.upsert_many(_ladder_rows(dates))
        await repos.advice_reports.upsert_many(_advice_rows())
        await repos.news_flash.upsert_many(_newsflash_rows())
        await repos.datasource_registry.upsert(
            "fake", "Fake", "fake", ["daily_bars", "limit_up_pool"], priority=100
        )

        # 复用 conftest 中的策略 / 因子注册 + 两个参数版本（供 ``/api/strategies``）。
        from app.factors.registry import sync_definitions as sync_factors
        from app.strategies.registry import sync_definitions as sync_strategies

        await sync_strategies(repos)
        await sync_factors(repos)
        draft = await repos.strategy_configs.create_draft(
            "dragon", {"base_position": 0.20}, "v1", created_by="perf-seed"
        )
        await repos.strategy_configs.activate("dragon", int(draft.version))
        draft = await repos.strategy_configs.create_draft(
            "dragon", {"base_position": 0.25}, "v2", created_by="perf-seed"
        )
        await repos.strategy_configs.activate("dragon", int(draft.version))

        await session.commit()

    return {
        "start": TRADE_DATE_START,
        "end": TRADE_DATE_END,
        "num_stocks": NUM_STOCKS,
        "num_bars_per_stock": NUM_BARS_PER_STOCK,
        "num_sentiment_rows": NUM_SENTIMENT_ROWS,
        "num_pool_rows": NUM_POOL_ROWS,
        "num_ladder_rows": NUM_LADDER_ROWS,
        "num_advice_rows": NUM_ADVICE_ROWS,
    }


def read_endpoints(seed_meta: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """返回压测用的读接口 (path, params) 列表。

    - 时间窗口取自种子 ``start`` / ``end``，保证 ``/api/bars/daily`` 与 ``/api/ladder``
      能命中真实数据；
    - 选择这 6 个端点是为了**避免单一接口缓存偏移**：
      含分页列表 / 含日期范围 / 含新鲜度字段 / 含子资源 / 配置类 / 单资源。
    """
    start = seed_meta["start"].isoformat()
    end = seed_meta["end"].isoformat()
    return [
        ("/api/sentiment", {"date": end}),
        ("/api/sentiment/history", {"days": 20}),
        ("/api/ladder", {"start": start, "end": end, "page": 1, "page_size": 50}),
        ("/api/themes", {"date": end}),
        ("/api/newsflash", {"limit": 20}),
        ("/api/stocks", {"page": 1, "page_size": 20}),
    ]


__all__ = [
    "NUM_ADVICE_ROWS",
    "NUM_BARS_PER_STOCK",
    "NUM_LADDER_ROWS",
    "NUM_POOL_ROWS",
    "NUM_SENTIMENT_ROWS",
    "NUM_STOCKS",
    "TRADE_DATE_END",
    "TRADE_DATE_START",
    "read_endpoints",
    "seed_perf_dataset",
]
