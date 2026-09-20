"""Task 6：真实数据源（hithink / xuangutong）provider + 声明式映射的离线测试。

全程离线、无 PostgreSQL/Redis：provider 注入 ``httpx.MockTransport``，把 ``tests/fixtures/``
下录制的真实形状 payload 喂给**真实的** ``fetch`` → ``apply_mapping`` → ``validate_records``
链路，从而在不触网的前提下验证端点/鉴权/单位换算/缺失拒绝/注册顺序。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from app.core.errors import UpstreamError
from app.datasources.base import reset_buckets
from app.datasources.contracts import (
    CAPABILITY_CONTRACTS,
    ContractValidationError,
    validate_records,
)
from app.datasources.mappings import apply_mapping, get_mapping, resolve_transform
from app.datasources.providers import hithink, install_real_capability_order
from app.datasources.providers.hithink import HithinkProvider
from app.datasources.providers.xuangutong import XuangutongProvider
from app.datasources.registry import (
    all_providers,
    assert_registry_consistent,
    get_provider,
    reset_capability_order,
    resolve_order,
    set_capability_order,
)
from app.datasources.resolve import resolve

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SH = ZoneInfo("Asia/Shanghai")

HITHINK_CAPABILITIES = ("daily_bars", "limit_up_pool", "ladder", "trading_calendar")
XUANGUTONG_CAPABILITIES = (
    "market_sentiment",
    "limit_up_pool",
    "theme_rank",
    "theme_stocks",
    "newsflash",
)
ALL_CASES = [("hithink", cap) for cap in HITHINK_CAPABILITIES] + [
    ("xuangutong", cap) for cap in XUANGUTONG_CAPABILITIES
]
#: 各能力的取数参数（无参能力省略）。这些参数同时作为映射上下文（``{"args": ...}``），
#: 供 ``theme_rank`` / ``theme_stocks`` 的 ``trade_date`` 从上下文派生。
FETCH_ARGS: dict[tuple[str, str], dict[str, Any]] = {
    ("hithink", "daily_bars"): {
        "thscode": "600519.SH",
        "start_ms": 1789315200000,
        "end_ms": 1789660800000,
    },
    ("hithink", "limit_up_pool"): {"date_ms": 1789660800000},
    ("xuangutong", "market_sentiment"): {"date": "2026-09-18"},
    ("xuangutong", "limit_up_pool"): {"date": "2026-09-18"},
    ("xuangutong", "theme_rank"): {"date": "2026-09-18"},
    ("xuangutong", "theme_stocks"): {"date": "2026-09-18", "type": "normal"},
    ("xuangutong", "newsflash"): {"limit": 20},
}

#: 每份 fixture 的预期记录数（ladder / theme_stocks 需按子列表摊平后计数）。
EXPECTED_COUNTS = {
    ("hithink", "daily_bars"): 5,
    ("hithink", "limit_up_pool"): 3,
    ("hithink", "ladder"): 7,  # 09-17 共 3 票 + 09-18 共 4 票
    ("hithink", "trading_calendar"): 3,
    ("xuangutong", "market_sentiment"): 1,  # 分钟点列表取最后一个（收盘）
    ("xuangutong", "limit_up_pool"): 2,
    ("xuangutong", "theme_rank"): 4,
    ("xuangutong", "theme_stocks"): 4,  # 3 行按 plates[] 展开为 4 条（一票两题材）
    ("xuangutong", "newsflash"): 3,
}


@pytest.fixture(autouse=True)
def _isolate_state() -> Any:
    """每个用例前后重置限流桶与能力顺序覆盖，并重装真实源默认顺序。"""
    reset_buckets()
    reset_capability_order()
    install_real_capability_order()
    yield
    reset_buckets()
    reset_capability_order()
    install_real_capability_order()


def load_fixture(source: str, capability: str) -> dict[str, Any]:
    """读取录制 fixture（上游原始形状，含 code/message 信封）。"""
    return json.loads((FIXTURES / source / f"{capability}.json").read_text(encoding="utf-8"))


def _transport(payload: dict[str, Any]) -> httpx.MockTransport:
    """构造恒返回该 payload 的离线 transport。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _provider(source: str, client: httpx.AsyncClient) -> Any:
    """按源构造注入了离线 client 与假凭证的 provider。"""
    if source == "hithink":
        return HithinkProvider(client=client, api_key="test-key")
    return XuangutongProvider(client=client, token="test-token")


async def fetch_rows(source: str, capability: str, **args: Any) -> tuple[list[Any], dict[str, Any]]:
    """离线走通 fetch → 映射 → 契约校验，返回 (契约对象, 原始 payload)。

    取数参数会作为映射上下文传入（``context={"args": ...}``），供 ``theme_rank`` /
    ``theme_stocks`` 这类「目标交易日不在 payload 内」的能力派生 ``trade_date``。
    """
    payload = load_fixture(source, capability)
    call_args = {**FETCH_ARGS.get((source, capability), {}), **args}
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        provider = _provider(source, client)
        raw = await provider.fetch(capability, **call_args)
    rows = apply_mapping(
        get_mapping(source, capability), raw, context={"args": call_args}
    )
    model = CAPABILITY_CONTRACTS[capability]
    objects = validate_records(model, rows, source=source, capability=capability)
    return objects, raw


# ============================================================ 1. 每个能力端到端可映射


@pytest.mark.parametrize(("source", "capability"), ALL_CASES)
async def test_each_capability_maps_fixture_to_contract(source: str, capability: str) -> None:
    """每个能力：fetch（离线）→ 映射 → 契约校验，记录数与契约类型均正确。"""
    objects, raw = await fetch_rows(source, capability)
    assert len(objects) == EXPECTED_COUNTS[(source, capability)]
    assert all(isinstance(obj, CAPABILITY_CONTRACTS[capability]) for obj in objects)
    # fetch 返回的是**原始信封**（未归一化）
    assert raw["code"] in (0, 20000)
    assert "data" in raw


# ============================================================ 2. hithink 单位换算与代码归一


async def test_hithink_daily_bars_units() -> None:
    """日线：.SH 归一、ms→date、volume(已是股)与 turnover(已是元)直传、源无昨收→None。"""
    rows, _ = await fetch_rows(
        "hithink", "daily_bars", thscode="600519.SH", start_ms=1789315200000, end_ms=1789660800000
    )
    assert len(rows) == 5
    bar = rows[0]
    assert bar.code == "600519.SH"  # normalize_code（^thscode 来自祖先作用域）
    assert bar.trade_date == date(2026, 9, 14)  # ms_to_date
    assert (bar.open, bar.high, bar.low, bar.close) == (1277.27, 1285.53, 1270.36, 1277.96)
    assert bar.volume_shares == 1_657_146  # 源已是股，不放大
    assert bar.amount_yuan == pytest.approx(2_116_621_850.13)  # 源已是元，不放大
    assert bar.pre_close is None  # 该端点不返回昨收 → 留空（绝不填 0）
    assert rows[-1].trade_date == date(2026, 9, 18)


async def test_hithink_limit_up_pool_units() -> None:
    """涨停池：HH:MM 透传、seal_money 已是元；源不提供的换手率/成交额/市值留空。"""
    rows, _ = await fetch_rows("hithink", "limit_up_pool", date_ms=1789660800000)
    assert len(rows) == 3
    row = rows[0]
    assert row.code == "001216.SZ"
    assert row.name == "华瓷股份"
    assert row.limit_up_time == "09:30"  # hhmm_from_str（源已是 HH:MM）
    assert row.continue_days == 4
    assert row.seal_amount_yuan == pytest.approx(98_271_095.0)  # 源已是元，不放大
    assert row.open_times is None  # 该端点不提供炸板次数
    assert row.turnover_rate is None  # 该端点不提供换手率
    assert row.amount_yuan is None  # 该端点不提供成交额
    assert row.market_cap_yuan is None  # 该端点不提供总市值
    assert row.pool_type == "limit_up"  # static 注入
    assert [r.code for r in rows] == ["001216.SZ", "603248.SH", "002285.SZ"]


async def test_hithink_ladder_and_trading_calendar() -> None:
    """天梯：按 boards 分组摊平为「一票一行」，日期由 ^date 回归；日历 is_open 恒真。"""
    ladder, _ = await fetch_rows("hithink", "ladder")
    assert len(ladder) == 7  # 09-17: 2板×2 + 3板×1；09-18: 2板×2 + 3板×1 + 4板×1
    assert ladder[0].trade_date == date(2026, 9, 17)  # str_to_date（^date 祖先作用域）
    assert ladder[0].code == "600448.SH"
    assert ladder[0].continue_days == 2  # board_num
    assert ladder[2].continue_days == 3
    assert ladder[-1].trade_date == date(2026, 9, 18)
    assert ladder[-1].continue_days == 4
    assert all(row.first_seal_time is None for row in ladder)  # 源缺该可选字段 → None

    days, _ = await fetch_rows("hithink", "trading_calendar")
    assert [(day.trade_date, day.is_open) for day in days] == [
        (date(2025, 9, 19), True),
        (date(2025, 9, 22), True),
        (date(2025, 9, 23), True),
    ]


# ============================================================ 3. xuangutong 单位换算与路径


async def test_xuangutong_market_sentiment_ratio_passthrough() -> None:
    """情绪：分钟点列表取最后一个（收盘）；源已是小数口径（ratio_passthrough）；stage 留空。"""
    rows, _ = await fetch_rows("xuangutong", "market_sentiment", date="2026-09-18")
    assert len(rows) == 1
    senti = rows[0]
    assert senti.trade_date == date(2026, 9, 18)  # sec_to_date（timestamp 为秒）
    assert senti.temperature == pytest.approx(69.61658753439677)
    assert senti.stage is None  # 该源无情绪阶段名 → 留空（由上层状态机派生）
    assert (senti.limit_up_count, senti.limit_down_count) == (79, 1)
    assert senti.broken_board_count == 26  # limit_up_broken_count
    assert senti.broken_rate == pytest.approx(0.24761904761904763)  # 源已是小数
    assert senti.premium_rate == pytest.approx(0.025003532704081635)  # yesterday_limit_up_avg_pcp
    assert (senti.up_count, senti.down_count) == (3937, 1109)
    assert senti.max_continue_days == 4  # lianbangaodu 字典 {"1":67,...,"4":2} 取最大键


async def test_xuangutong_limit_up_pool_units() -> None:
    """涨停池：.SS→.SH、秒→HH:MM、换手率/市值透传；源不提供封单金额与成交额 → 留空。"""
    rows, _ = await fetch_rows("xuangutong", "limit_up_pool", date="2026-09-18")
    assert len(rows) == 2
    row = rows[0]
    assert row.code == "600847.SH"  # normalize_code
    assert row.name == "万里股份"
    assert row.continue_days == 1  # limit_up_days
    assert row.limit_up_time == "09:49"  # hhmm_from_sec
    assert row.seal_amount_yuan is None  # 该端点不提供封单金额
    assert row.amount_yuan is None  # 该端点不提供成交额
    assert row.open_times == 2  # break_limit_up_times
    assert row.turnover_rate == pytest.approx(0.0650443611)  # 源已是小数
    assert row.market_cap_yuan == pytest.approx(2_133_760_608.0)  # 元，透传
    assert rows[1].code == "603001.SH"
    assert rows[1].limit_up_time == "09:53"
    assert rows[1].open_times == 7


async def test_xuangutong_theme_rank_and_stocks() -> None:
    """题材排名：数组顺序派生 rank、trade_date 取自上下文；题材个股：二维表 + plates 展开。"""
    ranks, _ = await fetch_rows("xuangutong", "theme_rank")
    assert len(ranks) == 4
    assert [r.rank for r in ranks] == [1, 2, 3, 4]  # rank_from_index
    assert ranks[0].name == "次新股"
    assert ranks[1].name == "国产芯片"
    assert all(r.trade_date == date(2026, 9, 18) for r in ranks)  # context=args.date
    assert ranks[0].core_avg_pct is None  # 该源不提供核心股涨幅
    assert ranks[0].core_count is None  # 该源不提供核心股数量
    assert ranks[3].description is None  # 末条无 description（可选字段 → None）

    stocks, _ = await fetch_rows("xuangutong", "theme_stocks")
    assert len(stocks) == 4  # 3 行按 plates[] 展开为 4 条
    first = stocks[0]
    assert first.theme_name == "机器人"  # 展开项 name（记录本身）
    assert first.code == "001216.SZ"  # ^code 回取父行
    assert first.name == "华瓷股份"  # ^prod_name 回取父行
    assert first.price == pytest.approx(18.42)  # ^cur_price
    assert first.pct == pytest.approx(0.1003)  # ^px_change_rate（源已是小数）
    assert first.turnover_rate == pytest.approx(0.0821093)
    assert first.continue_days == 4  # "4天4板" → 4
    assert stocks[1].theme_name == "国产芯片"  # 同一只个股的第二个题材
    assert stocks[2].continue_days is None  # 空连板文本 → 留空
    assert stocks[3].code == "002285.SZ"
    assert stocks[3].continue_days == 3  # "3天3板" → 3
    assert all(s.trade_date == date(2026, 9, 18) for s in stocks)


async def test_xuangutong_newsflash() -> None:
    """快讯：秒→aware datetime、整数级别转字符串、关联证券列表、分类 id 列表。"""
    rows, _ = await fetch_rows("xuangutong", "newsflash", limit=20)
    assert len(rows) == 3
    news = rows[0]
    assert news.ts == datetime(2026, 9, 19, 9, 11, 32, tzinfo=SH)  # sec_to_datetime
    assert news.level == "2"  # impact 整数 → 字符串
    assert news.title == "商务部新闻发言人就中美经贸磋商有关问题答记者问"
    assert news.symbols == []  # stocks 为空
    assert news.categories == ["457", "9", "10"]

    assert rows[1].symbols == ["301165.SZ"]  # stocks[].symbol
    assert rows[1].summary == ""  # 源为空串时保持空串（不拒绝记录）
    assert rows[2].ts == datetime(2026, 9, 18, 13, 20, 10, tzinfo=SH)
    assert rows[2].categories == ["723", "9"]


# ============================================================ 4. 缺失必需字段被拒绝


async def test_missing_required_field_is_rejected_not_zeroed() -> None:
    """必需字段缺失 → ContractValidationError 指名源与字段，且不产出任何记录。"""
    payload = load_fixture("hithink", "limit_up_pool")
    del payload["data"]["item"][0]["seal_money"]  # 必需源字段缺失

    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        raw = await _provider("hithink", client).fetch("limit_up_pool")

    produced: list[dict[str, Any]] | None = None
    issues = []
    try:
        produced = apply_mapping(get_mapping("hithink", "limit_up_pool"), raw)
    except ContractValidationError as exc:
        issues = exc.issues

    assert produced is None, "非法记录不得被产出（绝不填 0/空串）"
    assert any(issue.field == "seal_amount_yuan" for issue in issues)
    assert any("缺失" in issue.reason for issue in issues)
    assert all(issue.source == "hithink" for issue in issues)
    assert all(issue.capability == "limit_up_pool" for issue in issues)


# ============================================================ 5. 代码归一约定


def test_code_normalization_matches_framework_convention() -> None:
    """代码归一：选股通 ``.SS`` 归一为项目标准 ``.SH``；0/3→.SZ、4/8/9→.BJ。"""
    normalize = resolve_transform("normalize_code")
    assert normalize("600000.SS") == "600000.SH"
    assert normalize("600847.SS") == "600847.SH"
    assert normalize("000001.SZ") == "000001.SZ"
    assert normalize("300750.SZ") == "300750.SZ"
    assert normalize("830799.BJ") == "830799.BJ"


# ============================================================ 6. 注册与主备顺序


def test_both_providers_registered_and_ordered() -> None:
    """两个 provider 均已注册，且出现在各自能力的 resolve_order 中。"""
    assert get_provider("hithink") is HithinkProvider
    assert get_provider("xuangutong") is XuangutongProvider
    assert {"hithink", "xuangutong"} <= {cls.source_id for cls in all_providers()}

    for capability in HITHINK_CAPABILITIES:
        assert "hithink" in resolve_order(capability), capability
    for capability in XUANGUTONG_CAPABILITIES:
        assert "xuangutong" in resolve_order(capability), capability

    # 主源判定（对齐参考项目 CAPABILITY_PROVIDERS / DATA_CONTRACT.md）
    assert resolve_order("daily_bars")[0] == "hithink"
    assert resolve_order("ladder")[0] == "hithink"
    assert resolve_order("trading_calendar")[0] == "hithink"
    assert resolve_order("limit_up_pool")[0] == "hithink"
    assert resolve_order("market_sentiment")[0] == "xuangutong"
    assert resolve_order("newsflash")[0] == "xuangutong"

    assert_registry_consistent()  # 启动一致性断言仍然通过


def test_every_declared_capability_has_mapping() -> None:
    """provider 声明的每个能力都有 (源, 能力) 映射。"""
    for cls in (HithinkProvider, XuangutongProvider):
        for capability in cls.capabilities:
            assert get_mapping(cls.source_id, capability) is not None


# ============================================================ 7. 全链路离线 resolve


async def test_resolve_uses_real_provider_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve 走真实 provider 代码路径（离线）：hithink 为 daily_bars 主源。"""
    payload = load_fixture("hithink", "daily_bars")
    monkeypatch.setattr(
        hithink, "_make_client", lambda: httpx.AsyncClient(transport=_transport(payload))
    )
    monkeypatch.setattr(hithink, "get_settings", lambda: SimpleNamespace(hithink_api_key="k"))
    set_capability_order("daily_bars", ["hithink"])

    rows = await resolve("daily_bars", thscode="600519.SH", start_ms=1, end_ms=2)

    assert len(rows) == 5
    assert rows[0].code == "600519.SH"
    assert rows[0].volume_shares == 1_657_146


# ============================================================ 8. 配置缺失/上游异常优雅失败


async def test_hithink_without_api_key_raises_before_network() -> None:
    """未配置 API Key → 立即 UpstreamError（不触网、不崩溃）。"""
    provider = HithinkProvider(api_key="")
    with pytest.raises(UpstreamError) as excinfo:
        await provider.fetch("ladder")
    assert "API Key" in str(excinfo.value)
    assert excinfo.value.detail["source"] == "hithink"


async def test_newsflash_without_token_raises_upstream_error() -> None:
    """快讯缺 x-ivanka-token → 明确的 UpstreamError（可选配置，优雅降级）。"""
    provider = XuangutongProvider(token="")
    with pytest.raises(UpstreamError) as excinfo:
        await provider.fetch("newsflash")
    assert "x-ivanka-token" in str(excinfo.value)
    assert excinfo.value.detail["setting"] == "xuangutong_api_key"


async def test_hithink_business_error_code_raises() -> None:
    """业务码非 0（如 2001 密钥无效）→ UpstreamError，detail 携带 code。"""
    payload = {"code": 2001, "message": "invalid key"}
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        provider = HithinkProvider(client=client, api_key="k")
        with pytest.raises(UpstreamError) as excinfo:
            await provider.fetch("ladder")
    assert excinfo.value.detail["code"] == 2001


async def test_xuangutong_token_invalid_code_raises() -> None:
    """快讯 code=50008（token 无效）→ UpstreamError。"""
    payload = {"code": 50008, "message": "token invalid"}
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        provider = XuangutongProvider(client=client, token="bad")
        with pytest.raises(UpstreamError) as excinfo:
            await provider.fetch("newsflash")
    assert excinfo.value.detail["code"] == 50008
