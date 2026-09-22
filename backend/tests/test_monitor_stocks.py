"""监管名单（``monitor_stocks`` / ``monitor_unusual``）采集链路测试。

全程离线：provider 注入 ``httpx.MockTransport``，把 ``tests/fixtures/eastmoney/``
下**录制的真实响应**（2026-09-22 抓取）喂给真实的 ``fetch`` → ``apply_mapping`` →
``validate_records`` 链路；写入器走内存 SQLite。

覆盖点：

1. 多列源（``FieldMap.source`` 元组）与 ``market_code`` 转换——东财重点监控把交易所
   外置成 ``MARKET``，标准代码必须双列拼装（仅凭首位数字会把 513390 等基金判错）；
2. 两个端点的信封校验（裸数组 / 数据中心）与错误分支；
3. 两份声明式映射的字段口径（含 ``YYYY-MM-DD 00:00:00`` 日期串、可缺字段）；
4. 写入器的**批内去重**（唯一键 ``(trade_date, kind, code)`` 撞车会让 PG 报
   ``cannot affect row a second time``）与幂等；
5. 取数参数、任务注册（盘后窗口 / 30 分钟 / 两个能力共用写入器）、缓存失效事件。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.core.errors import UpstreamError
from app.datasources.base import reset_buckets
from app.datasources.contracts import CAPABILITY_CONTRACTS, validate_records
from app.datasources.mappings import apply_mapping, get_mapping, resolve_transform
from app.datasources.mappings.transforms import DEGRADED
from app.datasources.providers.eastmoney import EastmoneyProvider
from app.datasources.registry import assert_registry_consistent
from app.db.base import Base
from app.ingest.events import CAPABILITY_EVENTS
from app.ingest.tasks import (
    DEFAULT_TASKS,
    MONITOR_UNUSUAL_PAGES,
    WRITERS,
    _dedupe_monitor_rows,
    _monitor_stocks_args,
    _monitor_unusual_args,
    _write_monitor_stocks,
)
from app.models.market import MonitorStock
from app.repositories import Repositories
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eastmoney"
DAY = date(2026, 9, 22)
#: 重点监控端点在 fixture 里的真实条数。
RESTRICTED_ROWS = 15


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> EastmoneyProvider:
    """构造离线 provider（client 由调用方持有，provider 不负责关闭）。"""
    return EastmoneyProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _datacenter_handler(captured: list[httpx.Request]) -> Callable[..., httpx.Response]:
    """按 ``filter`` 回放 002 / 001 的真实响应，并记录请求供参数断言。"""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        filt = request.url.params.get("filter", "")
        name = "unusual_001.json" if '"001"' in filt else "unusual_002.json"
        return httpx.Response(200, json=_load(name))

    return handler


@pytest.fixture(autouse=True)
def _fresh_buckets() -> Iterator[None]:
    """每例重置按源独立的令牌桶，避免限流状态跨用例串台。"""
    reset_buckets()
    yield
    reset_buckets()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 会话；每例独立建库以保证隔离。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
        yield opened
    await engine.dispose()


# ============================================================ 1. 多列源 + market_code


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (("688121", "1"), "688121.SH"),  # 沪市科创板
        (("513390", "1"), "513390.SH"),  # 沪市基金：数字规则会判错，必须靠 MARKET
        (("159509", "0"), "159509.SZ"),  # 深市 ETF
        (("000001", "0"), "000001.SZ"),
        (("920575", "b"), "920575.BJ"),  # 北交所；小写标记亦可
        (("000017", "深交所"), "000017.SZ"),  # 数据中心的中文交易所口径
        (("688137", "上交所"), "688137.SH"),
        (("920023", "北交所"), "920023.BJ"),
    ],
)
def test_market_code_assembles_standard_code(value: tuple[str, str], expected: str) -> None:
    """代码 + 交易所标记 → 标准代码（交易所以上游标注为准）。"""
    assert resolve_transform("market_code")(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        ("688121", None),  # 缺交易所列
        (None, "1"),  # 缺代码列
        ("68812", "1"),  # 非 6 位
        ("688121", "X"),  # 未知交易所标记
        ("688121",),  # 元组长度不对
        "688121",  # 不是元组（单值不支持）
        ("688121", "1", "extra"),
    ],
)
def test_market_code_degrades_instead_of_guessing(value: Any) -> None:
    """信息不足一律 ``DEGRADED``（绝不猜交易所）。"""
    assert resolve_transform("market_code")(value) is DEGRADED


def test_dsl_tuple_source_resolves_multiple_columns() -> None:
    """``FieldMap.source`` 写成元组 = 同一条记录取多列，整体交给 transform。

    以真实映射（``monitor_stocks`` 的 ``code``）验证，不另造平行结构。
    """
    mapping = get_mapping("eastmoney", "monitor_stocks")
    field = next(f for f in mapping.fields if f.target == "code")
    assert field.source == ("STKCODE", "MARKET")
    assert field.transform == "market_code"

    rows = apply_mapping(
        mapping,
        {"data": [{"STKCODE": "513390", "MARKET": "1", "STKNAME": "纳指基金"}]},
        context={"args": {"date": DAY.isoformat()}},
    )
    assert rows[0]["code"] == "513390.SH"


def test_dsl_tuple_source_missing_part_rejects_required_field() -> None:
    """多列源任一分量缺失 → 整体缺失 → 必需字段被拒绝（不产出半条记录）。"""
    from app.datasources.contracts import ContractValidationError

    mapping = get_mapping("eastmoney", "monitor_stocks")
    with pytest.raises(ContractValidationError) as excinfo:
        apply_mapping(
            mapping,
            {"data": [{"STKCODE": "688121", "STKNAME": "缺交换所有"}]},
            context={"args": {"date": DAY.isoformat()}},
        )
    assert "code" in str(excinfo.value)


# ============================================================ 2. provider：重点监控


async def test_provider_restricted_wraps_bare_array() -> None:
    """裸数组端点：provider 套 ``{"data": [...]}``（只包不改），映射可直接消费。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_load("stock_monitor.json"))

    provider = _provider(handler)
    payload = await provider.fetch("monitor_stocks")

    assert set(payload) == {"data"}
    assert len(payload["data"]) == RESTRICTED_ROWS
    # 字段名保持上游原样（未在 provider 层重命名）
    assert {"STKCODE", "MARKET", "STKNAME"} <= set(payload["data"][0])
    request = captured[0]
    assert request.url.path.endswith("/emcfg/stock_monitor.json")
    assert request.headers["Referer"].startswith("https://vipmoney.eastmoney.com/")
    assert "iPhone" in request.headers["User-Agent"]


async def test_provider_restricted_rejects_non_array() -> None:
    """该端点若返回对象（形状变了）应明确报错，而不是静默产出 0 条。"""
    provider = _provider(lambda _: httpx.Response(200, json={"oops": 1}))
    with pytest.raises(UpstreamError) as excinfo:
        await provider.fetch("monitor_stocks")
    assert "不是数组" in str(excinfo.value)


async def test_provider_rejects_unknown_capability() -> None:
    provider = _provider(lambda _: httpx.Response(200, json=[]))
    with pytest.raises(UpstreamError) as excinfo:
        await provider.fetch("daily_bars")
    assert "不支持能力" in str(excinfo.value)


# ============================================================ 3. provider：异常波动


@pytest.mark.parametrize(("kind", "unusual_type"), [("severe", "002"), ("unusual", "001")])
async def test_provider_unusual_query_params(kind: str, unusual_type: str) -> None:
    """查询参数口径：报表名 / 过滤 / 排序 / 分页 / 来源。"""
    captured: list[httpx.Request] = []
    provider = _provider(_datacenter_handler(captured))
    payload = await provider.fetch("monitor_unusual", kind=kind, page=3)

    request = captured[0]
    params = request.url.params
    assert request.url.path.endswith("/securities/api/data/v1/get")
    assert params["reportName"] == "RPT_APP_UNUSUALBASIC"
    assert params["filter"] == f'(UNUSUAL_TYPE="{unusual_type}")'
    assert params["sortColumns"] == "NOTICE_DATE,END_DATE"
    assert params["sortTypes"] == "-1,-1"
    assert params["pageNumber"] == "3"
    assert params["pageSize"] == "50"
    assert params["source"] == "SECURITIES" and params["client"] == "APP"
    assert "MRAKET_TYPE" in params["columns"]
    # 信封原样保留（含 result.count，供排障/分页观察）
    assert payload["result"]["count"] > 0
    assert isinstance(payload["result"]["data"], list)


async def test_provider_unusual_rejects_unknown_kind() -> None:
    provider = _provider(_datacenter_handler([]))
    with pytest.raises(UpstreamError) as excinfo:
        await provider.fetch("monitor_unusual", kind="bogus")
    assert "不支持类型" in str(excinfo.value)


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ({"code": 1, "success": False, "message": "boom"}, "业务错误"),
        ({"code": 0, "success": True}, "缺少 result"),
        (["not", "an", "object"], "不是对象"),
    ],
)
async def test_provider_unusual_rejects_bad_envelope(body: Any, needle: str) -> None:
    """业务码异常 / 结构不符都上抛 UpstreamError，不吞错。"""
    provider = _provider(lambda _: httpx.Response(200, json=body))
    with pytest.raises(UpstreamError) as excinfo:
        await provider.fetch("monitor_unusual", kind="severe")
    assert needle in str(excinfo.value)


# ============================================================ 4. 映射 → 契约


def _map(capability: str, payload: Any, kind: str | None = None) -> list[Any]:
    args: dict[str, Any] = {"date": DAY.isoformat()}
    if kind is not None:
        args["kind"] = kind
    rows = apply_mapping(get_mapping("eastmoney", capability), payload, context={"args": args})
    return validate_records(
        CAPABILITY_CONTRACTS[capability], rows, source="eastmoney", capability=capability
    )


async def test_mapping_restricted_end_to_end() -> None:
    """重点监控：15 条全部过契约，交易所分布与上游 MARKET 标注一致。"""
    handler = lambda _: httpx.Response(200, json=_load("stock_monitor.json"))  # noqa: E731
    payload = await _provider(handler).fetch("monitor_stocks")
    records = _map("monitor_stocks", payload)

    assert len(records) == RESTRICTED_ROWS
    assert all(rec.kind == "restricted" for rec in records)  # static 注入
    assert all(rec.trade_date == DAY for rec in records)  # 取自 args.date
    assert all("." in rec.code for rec in records)

    upstream = _load("stock_monitor.json")
    # 交易所标记 1=沪 / 0=深 / B=北 的分布应与契约里的代码后缀完全一致（11/1/3）。
    want = {"1": "SH", "0": "SZ", "B": "BJ"}
    from collections import Counter

    assert Counter(rec.code.split(".")[-1] for rec in records) == Counter(
        want[row["MARKET"]] for row in upstream
    )
    # 该端点无原因文本 / 公告日 / 公告编号 → 留空而不是填 0/空串
    assert all(
        rec.reason is None and rec.notice_date is None and rec.info_code is None for rec in records
    )
    # 监控有效期已落
    assert all(rec.start_date is not None and rec.end_date is not None for rec in records)
    # 部分条目自身缺 LINK_URL → 对应记录为 None（其余有值）
    assert any(rec.link_url is None for rec in records)
    assert any(rec.link_url is not None for rec in records)


@pytest.mark.parametrize(
    ("kind", "fixture", "first_code"),
    [
        ("severe", "unusual_002.json", "000017.SZ"),
        ("unusual", "unusual_001.json", "688137.SH"),
    ],
)
async def test_mapping_unusual_end_to_end(kind: str, fixture: str, first_code: str) -> None:
    """异常波动：datetime 串解析、原因/分类/公告编号齐全，kind 取自 args。"""
    payload = await _provider(lambda _: httpx.Response(200, json=_load(fixture))).fetch(
        "monitor_unusual", kind=kind
    )
    records = _map("monitor_unusual", payload, kind=kind)

    assert len(records) == len(payload["result"]["data"]) == 3
    for rec in records:
        assert rec.kind == kind
        assert rec.trade_date == DAY
        assert rec.name and rec.reason and rec.info_code
        # "2026-08-20 00:00:00" 这类 datetime 串已解析为 date（该源 START_DATE 可为 null）
        assert rec.end_date is not None and rec.notice_date is not None
        assert rec.reason_type
        assert rec.link_url is None  # 该端点无链接
        assert rec.code.split(".")[-1] in {"SH", "SZ", "BJ"}

    assert records[0].code == first_code
    assert records[0].name == payload["result"]["data"][0]["SECURITY_NAME_ABBR"]


def test_mapping_unusual_tolerates_null_start_date() -> None:
    """``START_DATE`` 为 null 是合法的（上游确实会给）→ 留 None 而非拒绝整行。"""
    payload = {
        "code": 0,
        "success": True,
        "result": {
            "count": 1,
            "pages": 1,
            "data": [
                {
                    "SECURITY_CODE": "002731",
                    "MRAKET_TYPE": "深交所",
                    "SECURITY_NAME_ABBR": "*ST萃华",
                    "START_DATE": None,
                    "END_DATE": "2026-08-28 00:00:00",
                    "NOTICE_DATE": "2026-08-31 00:00:00",
                    "INFO_CODE": "AN202608301828734784",
                    "UNUSUAL_REASON": "公司股票交易异常波动",
                    "UNUSUAL_REASON_TYPE": "连续3个交易日内日收盘价格涨幅偏离值累计达到20%",
                }
            ],
        },
    }
    records = _map("monitor_unusual", payload, kind="severe")
    assert len(records) == 1
    assert records[0].start_date is None
    assert records[0].end_date == date(2026, 8, 28)


# ============================================================ 5. 写入器与去重


def test_dedupe_keeps_newest_notice() -> None:
    """同批重复 ``(kind, code)``：保留公告日最新的一条（否则 PG 会报冲突行重复）。"""
    rows = [
        {"kind": "severe", "code": "000017.SZ", "name": "旧", "notice_date": date(2026, 8, 1)},
        {"kind": "severe", "code": "000017.SZ", "name": "新", "notice_date": date(2026, 9, 3)},
        {"kind": "unusual", "code": "000017.SZ", "name": "另类", "notice_date": date(2026, 7, 1)},
        {"kind": "severe", "code": "920575.BJ", "name": "无公告日A", "notice_date": None},
        {"kind": "severe", "code": "920575.BJ", "name": "无公告日B", "notice_date": None},
    ]
    out = {f"{r['kind']}/{r['code']}": r for r in _dedupe_monitor_rows(rows)}

    assert len(out) == 3  # kind 不同不算重复
    assert out["severe/000017.SZ"]["name"] == "新"
    assert out["unusual/000017.SZ"]["name"] == "另类"
    assert out["severe/920575.BJ"]["name"] == "无公告日A"  # 都缺公告日 → 保留先出现的


class _FakeContract:
    """最小契约桩：写入器只读 ``model_dump()``。"""

    def __init__(self, **data: Any) -> None:
        self._data = {**data, "trade_date": DAY}

    def model_dump(self) -> dict[str, Any]:
        return dict(self._data)


async def test_writer_persists_and_dedupes(session: AsyncSession) -> None:
    """写入器：同批重复键先去重、落库字段完整、同日重跑幂等。"""
    repos = Repositories.build(session)
    source = "eastmoney"
    # 同一批里放两个同键（模拟上游一页含重复代码）+ 另一 kind 的同码
    batch = [
        _FakeContract(
            kind="severe",
            code="000017.SZ",
            name="深中华A(旧)",
            reason="旧公告",
            start_date=date(2026, 8, 20),
            end_date=date(2026, 9, 2),
            notice_date=date(2026, 9, 1),
            info_code="AN-OLD",
            reason_type="连续10个交易日涨幅偏离100%",
            link_url=None,
        ),
        _FakeContract(
            kind="severe",
            code="000017.SZ",
            name="深中华A",
            reason="新公告",
            start_date=date(2026, 8, 20),
            end_date=date(2026, 9, 2),
            notice_date=date(2026, 9, 3),
            info_code="AN-NEW",
            reason_type="连续10个交易日涨幅偏离100%",
            link_url=None,
        ),
        _FakeContract(
            kind="unusual",
            code="000017.SZ",
            name="深中华A",
            reason="普通异动",
            start_date=None,
            end_date=date(2026, 8, 28),
            notice_date=date(2026, 9, 4),
            info_code="AN-U",
            reason_type="连续3日涨幅偏离20%",
            link_url=None,
        ),
        _FakeContract(
            kind="restricted",
            code="513390.SH",
            name="纳指基金",
            reason=None,
            start_date=date(2026, 9, 22),
            end_date=date(2026, 10, 13),
            notice_date=None,
            info_code=None,
            reason_type=None,
            link_url="https://wap.18.cn/app/detail/818/2527",
        ),
    ]
    written = await _write_monitor_stocks(repos, batch, DAY, source)
    await session.flush()
    assert written == 3  # 4 条输入、同键去重后 3 条

    result = await session.execute(
        select(MonitorStock).order_by(MonitorStock.code, MonitorStock.kind)
    )
    rows = result.scalars().all()
    assert len(rows) == 3
    by_key = {(r.kind, r.code): r for r in rows}
    assert by_key[("severe", "000017.SZ")].info_code == "AN-NEW"  # 保留最新公告
    assert by_key[("severe", "000017.SZ")].notice_date == date(2026, 9, 3)
    assert by_key[("restricted", "513390.SH")].link_url.endswith("2527")
    assert by_key[("restricted", "513390.SH")].notice_date is None
    assert by_key[("unusual", "000017.SZ")].start_date is None
    assert all(r.trade_date == DAY and r.source == source for r in rows)

    # 重跑同一批：行数不变（幂等覆盖）
    await _write_monitor_stocks(repos, batch, DAY, source)
    await session.flush()
    assert int(await session.scalar(select(func.count()).select_from(MonitorStock)) or 0) == 3


async def test_writer_empty_batch_is_noop(session: AsyncSession) -> None:
    """空批（本轮无数据）不报错、不删已有一行。"""
    repos = Repositories.build(session)
    await _write_monitor_stocks(
        repos,
        [_FakeContract(kind="restricted", code="600519.SH", name="贵州茅台", reason=None)],
        DAY,
        "eastmoney",
    )
    assert await _write_monitor_stocks(repos, [], DAY, "eastmoney") == 0
    await session.flush()
    assert int(await session.scalar(select(func.count()).select_from(MonitorStock)) or 0) == 1


# ============================================================ 6. 取数参数与任务注册


async def test_monitor_args_builders() -> None:
    """重点监控单轮；异常波动按类型逐页（页数是显式上限）。"""
    assert await _monitor_stocks_args(DAY, None) == [{"date": DAY.isoformat()}]

    unusual = await _monitor_unusual_args(DAY, None)
    assert len(unusual) == sum(MONITOR_UNUSUAL_PAGES.values()) == 6
    assert [a["kind"] for a in unusual[:4]] == ["severe"] * 4
    assert [a["page"] for a in unusual[:4]] == [1, 2, 3, 4]
    assert [a["kind"] for a in unusual[4:]] == ["unusual"] * 2
    assert all(a["date"] == DAY.isoformat() for a in unusual)


def test_monitor_tasks_registered_in_postmarket() -> None:
    """两个监管名单任务：盘后窗口 + 30 分钟节拍 + 共用同一写入器。"""
    tasks = {t.name: t for t in DEFAULT_TASKS if t.name.startswith("monitor")}
    assert set(tasks) == {"monitor_stocks", "monitor_unusual"}

    for task in tasks.values():
        assert task.window is not None
        assert task.window.name == "postmarket"
        assert (task.window.start, task.window.end) == ("17:00", "18:00")
        assert task.interval_seconds == 1800
        assert task.target == "monitor_stocks"
        assert task.target in WRITERS

    assert tasks["monitor_stocks"].capability == "monitor_stocks"
    assert tasks["monitor_unusual"].capability == "monitor_unusual"


def test_monitor_capabilities_invalidate_read_cache() -> None:
    """两个能力都要失效 ``monitor`` 读缓存（页面用普通 useQuery，无需 WS 事件）。"""
    for capability in ("monitor_stocks", "monitor_unusual"):
        channel, namespaces = CAPABILITY_EVENTS[capability]
        assert channel is None
        assert "monitor" in namespaces


def test_registry_consistent_with_eastmoney() -> None:
    """注册表自洽：能力有契约、源已注册、(源, 能力) 有映射（漏一步启动即失败）。"""
    assert_registry_consistent()
    assert "monitor_stocks" in CAPABILITY_CONTRACTS
    assert "monitor_unusual" in CAPABILITY_CONTRACTS
    # 两个能力共用同一份领域契约
    assert CAPABILITY_CONTRACTS["monitor_stocks"] is CAPABILITY_CONTRACTS["monitor_unusual"]
