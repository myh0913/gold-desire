"""可插拔数据源框架测试：声明式映射、校验拒绝、主备降级、限流独立性、回放守卫。

全部离线运行（无网络、无 PostgreSQL/Redis）。fake provider 的 payload 刻意使用
与契约不同的字段名/单位，以真实地走通映射层。
"""

from __future__ import annotations

import copy
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import httpx
import pytest
from app.core.errors import SnapshotMissingError, UpstreamError
from app.datasources.base import (
    BaseProvider,
    SourceKind,
    TokenBucket,
    get_bucket,
    request_with_retry,
    reset_buckets,
)
from app.datasources.contracts import (
    CAPABILITY_CONTRACTS,
    DEGRADED,
    ContractValidationError,
    LimitUpStockContract,
)
from app.datasources.mappings import (
    CapabilityMapping,
    FieldMap,
    apply_mapping,
    get_mapping,
    load_yaml_mappings,
    resolve_transform,
)
from app.datasources.providers.fake import default_payloads
from app.datasources.registry import (
    RegistryError,
    all_providers,
    register_provider,
    reset_capability_order,
    resolve_order,
    set_capability_order,
)
from app.datasources.resolve import reset_replay_source, resolve, resolve_raw, set_replay_source
from pydantic import ValidationError as PydanticValidationError

APP_DIR = Path(__file__).resolve().parents[1] / "app"
PROVIDERS_DIR = APP_DIR / "datasources" / "providers"
SH = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def _isolate_state() -> Any:
    """每个用例前后重置限流桶、能力顺序与回放读取器。"""
    reset_buckets()
    reset_capability_order()
    reset_replay_source()
    yield
    reset_buckets()
    reset_capability_order()
    reset_replay_source()


# ============================================================ 测试用 provider


@register_provider
class AlwaysFailProvider(BaseProvider):
    """始终失败的源，用于验证主备降级。"""

    source_id: ClassVar[str] = "always_fail"
    label: ClassVar[str] = "始终失败源"
    kind: ClassVar[SourceKind] = SourceKind.FAKE
    capabilities: ClassVar[tuple[str, ...]] = ("limit_up_pool",)
    rate_limit_per_min: ClassVar[int] = 600

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        raise UpstreamError("模拟上游不可用", detail={"source": self.source_id})


@register_provider
class SpyProvider(BaseProvider):
    """记录 fetch 调用次数的源，用于验证回放守卫不会触达 provider。"""

    source_id: ClassVar[str] = "spy"
    label: ClassVar[str] = "调用计数源"
    kind: ClassVar[SourceKind] = SourceKind.FAKE
    capabilities: ClassVar[tuple[str, ...]] = ("limit_up_pool",)
    rate_limit_per_min: ClassVar[int] = 600

    calls: ClassVar[list[tuple[str, dict[str, Any]]]] = []

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        SpyProvider.calls.append((capability, args))
        return copy.deepcopy(default_payloads()[capability])


class BrokenProvider(BaseProvider):
    """返回缺字段 payload 的源，用于验证「校验失败也降级」。"""

    source_id: ClassVar[str] = "broken"
    label: ClassVar[str] = "缺字段源"
    kind: ClassVar[SourceKind] = SourceKind.FAKE
    capabilities: ClassVar[tuple[str, ...]] = ("limit_up_pool",)
    rate_limit_per_min: ClassVar[int] = 600

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        payload = copy.deepcopy(default_payloads()[capability])
        payload["data"]["items"][0].pop("symbol")
        return payload


register_provider(BrokenProvider)


# ============================================================ 1. 映射正确性


async def test_mapping_transforms_daily_bar() -> None:
    """日线映射：.SS→.SH、ms→date、手→股、万元→元。"""
    rows = await resolve("daily_bars")
    assert len(rows) == 1
    bar = rows[0]
    assert bar.code == "600519.SH"  # normalize_code（.SS 归一为 .SH）
    assert bar.trade_date == date(2026, 9, 18)  # ms_to_date（上海时区）
    assert bar.volume_shares == 123450  # 1234.5 手 × 100
    assert bar.amount_yuan == pytest.approx(34_567_800.0)  # 3456.78 万元 × 10000
    assert (bar.open, bar.close, bar.pre_close) == (1500.0, 1510.0, 1495.0)


async def test_mapping_transforms_limit_up_pool() -> None:
    """涨停池映射：pct_to_ratio、yuan_from_wan、yuan_from_yi、hhmm_from_ms、static。"""
    row = (await resolve("limit_up_pool"))[0]
    assert row.code == "300750.SZ"
    assert row.continue_days == 3
    assert row.limit_up_time == "09:25"  # 1789694700000ms → 2026-09-18 09:25 CST
    assert row.seal_amount_yuan == pytest.approx(12_345_000.0)  # 1234.5 万元
    assert row.amount_yuan == pytest.approx(456_789_000.0)  # 45678.9 万元
    assert row.market_cap_yuan == pytest.approx(120_000_000.0)  # 1.2 亿元
    assert row.turnover_rate == pytest.approx(0.125)  # 12.5% → 0.125
    assert row.pool_type == "limit_up"  # static 注入


async def test_mapping_transforms_str_to_date_and_lists() -> None:
    """str_to_date（YYYYMMDD）、list_of_str、ms_to_datetime。"""
    ladder = (await resolve("ladder"))[0]
    assert ladder.trade_date == date(2026, 9, 18)
    assert ladder.first_seal_time == "09:25"

    days = await resolve("trading_calendar")
    assert [(d.trade_date, d.is_open) for d in days] == [
        (date(2026, 9, 18), True),
        (date(2026, 9, 19), False),
    ]

    news = (await resolve("newsflash"))[0]
    assert news.ts == datetime(2026, 9, 19, 10, 15, tzinfo=SH)
    assert news.symbols == ["600519.SS", "000001.SZ"]
    assert news.categories == ["人工智能", "算力"]


def test_strip_suffix_keeps_six_digits() -> None:
    """strip_suffix：剥离交易所后缀保留 6 位数字（与 normalize_code 区分）。"""
    strip = resolve_transform("strip_suffix")
    assert strip("600519.SS") == "600519"
    assert strip("000001.SZ") == "000001"
    assert strip("830799.BJ") == "830799"
    assert strip("bad") is DEGRADED  # 非法输入返回哨兵，不填 0/空串


def test_remaining_transforms() -> None:
    """constant / ratio_passthrough / hhmm_from_str / yuan_from_yi 行为正确。"""
    assert resolve_transform("constant:limit_up")("任意值") == "limit_up"
    assert resolve_transform("ratio_passthrough")(0.125) == pytest.approx(0.125)
    assert resolve_transform("hhmm_from_str")("2026-09-18 09:30:05") == "09:30"
    assert resolve_transform("yuan_from_yi")(1.2) == pytest.approx(120_000_000.0)


def test_mapping_path_supports_list_index() -> None:
    """source 路径支持列表索引 "a.b[0].c"。"""
    mapping = CapabilityMapping(
        source_id="demo_http",
        capability="daily_bars",
        record_path=None,
        fields=(
            FieldMap("code", "data.items[0].symbol", "normalize_code"),
            FieldMap("trade_date", "data.items[0].date_ms", "ms_to_date"),
        ),
    )
    payload = {"data": {"items": [{"symbol": "600519.SS", "date_ms": 1789660800000}]}}
    rows = apply_mapping(mapping, payload)
    assert rows == [{"code": "600519.SH", "trade_date": date(2026, 9, 18)}]


# ============================================================ 2. 缺失必需字段被拒绝


def _payload_without(capability: str, key: str) -> dict[str, Any]:
    """复制 fake payload 并删除记录中的某个源字段。"""
    payload = copy.deepcopy(default_payloads()[capability])
    del payload["data"]["items"][0][key]
    return payload


@pytest.mark.parametrize(
    ("missing_key", "expected_field"),
    [("symbol", "code"), ("turnover", "turnover_rate")],
)
def test_missing_required_field_is_rejected_not_zeroed(
    missing_key: str, expected_field: str
) -> None:
    """必需源字段缺失 → ContractValidationError 列出字段，绝不产出 0/空串。"""
    mapping = get_mapping("fake", "limit_up_pool")
    produced: list[dict[str, Any]] | None = None
    issues = []
    try:
        produced = apply_mapping(mapping, _payload_without("limit_up_pool", missing_key))
    except ContractValidationError as exc:
        issues = exc.issues

    assert produced is None, "非法记录不得被产出"
    assert any(issue.field == expected_field for issue in issues)
    assert any("缺失" in issue.reason for issue in issues)
    assert issues[0].source == "fake"
    assert issues[0].capability == "limit_up_pool"
    assert issues[0].raw_pointer.startswith("records[0].")


# ============================================================ 3. extra="forbid"


async def test_unmapped_vendor_field_does_not_leak() -> None:
    """未映射的源字段不进入契约对象；模型本身拒绝额外字段。"""
    row = (await resolve("limit_up_pool"))[0]
    assert set(row.model_dump()) == set(LimitUpStockContract.model_fields)
    assert "symbol" not in row.model_dump()
    assert "seal_money" not in row.model_dump()

    with pytest.raises(PydanticValidationError):
        LimitUpStockContract.model_validate({**row.model_dump(), "vendor_extra": 1})


# ============================================================ 4. 主备降级


async def test_failover_to_secondary_source(caplog: pytest.LogCaptureFixture) -> None:
    """主源抛 UpstreamError → 自动降级到备源并记录结构化告警。"""
    set_capability_order("limit_up_pool", ["always_fail", "fake"])
    assert resolve_order("limit_up_pool") == ["always_fail", "fake"]

    with caplog.at_level("WARNING"):
        rows = await resolve("limit_up_pool")

    assert len(rows) == 1
    assert rows[0].code == "300750.SZ"
    assert any(record.source == "always_fail" for record in caplog.records)
    assert any("降级" in record.getMessage() for record in caplog.records)


async def test_validation_failure_triggers_failover() -> None:
    """契约校验失败（字段缺失）同样触发降级，而非返回残缺数据。"""
    set_capability_order("limit_up_pool", ["broken", "fake"])
    rows = await resolve("limit_up_pool")
    assert rows[0].code == "300750.SZ"  # 已降级到 fake 正常数据


async def test_all_sources_failed_raises_upstream_error() -> None:
    """所有候选源失败 → UpstreamError，detail 列出每个源的失败原因。"""
    set_capability_order("limit_up_pool", ["always_fail"])
    with pytest.raises(UpstreamError) as excinfo:
        await resolve("limit_up_pool")
    failures = excinfo.value.detail["failures"]
    assert [item["source"] for item in failures] == ["always_fail"]


async def test_production_disables_fake_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """生产环境剔除 fake 兜底源：真实源全挂时显式报错，绝不写假数据入库。"""
    from app.core.config import get_settings

    set_capability_order("limit_up_pool", ["always_fail", "fake"])
    monkeypatch.setenv("APP_ENV", "prod")
    get_settings.cache_clear()
    try:
        with pytest.raises(UpstreamError) as excinfo:
            await resolve("limit_up_pool")
        failures = excinfo.value.detail["failures"]
        assert [item["source"] for item in failures] == ["always_fail"], "fake 不应被尝试"
    finally:
        get_settings.cache_clear()


# ============================================================ 5. 无源分支守护


_CONCRETE_IMPORT_RE = re.compile(r"(?:from|import)\s+[\w.]*providers\.([A-Za-z_]\w*)")


def test_business_code_does_not_import_concrete_providers() -> None:
    """app/（providers/ 之外）不得 import 具体 provider 模块。"""
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if str(path).startswith(str(PROVIDERS_DIR)):
            continue
        code = path.read_text(encoding="utf-8")
        for match in _CONCRETE_IMPORT_RE.finditer(code):
            offenders.append(f"{path.relative_to(APP_DIR)} -> providers.{match.group(1)}")
    assert not offenders, "业务代码出现具体 provider 依赖:\n" + "\n".join(offenders)


def test_resolve_has_no_source_branching() -> None:
    """resolve.py 只做映射 + 校验：不得出现任何源判断分支。"""
    code = (APP_DIR / "datasources" / "resolve.py").read_text(encoding="utf-8")
    for forbidden in ("source_id ==", '== "fake"', '== "hithink"', "if source =="):
        assert forbidden not in code, f"resolve.py 出现源分支: {forbidden}"


# ============================================================ 6. 限流独立性


async def test_rate_limit_independence_between_sources() -> None:
    """A 源令牌耗尽不阻塞 B 源；令牌桶按源独立。"""
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    clock = {"t": 100.0}

    def now() -> float:
        return clock["t"]

    bucket_a = TokenBucket(1, clock=now, sleep=fake_sleep)  # 容量 1，速率 1/分钟
    bucket_b = TokenBucket(600, clock=now, sleep=fake_sleep)

    await bucket_a.acquire()
    assert waits == []  # 首个令牌立即可得

    await bucket_a.acquire()  # 耗尽后必须等待
    assert waits and waits[0] > 0

    waits.clear()
    await bucket_b.acquire()  # B 源不受 A 源影响
    assert waits == []

    assert get_bucket("src_a", 1) is get_bucket("src_a", 1)
    assert get_bucket("src_a", 1) is not get_bucket("src_b", 600)


# ============================================================ 7. 回放守卫


async def test_replay_guard_raises_and_never_calls_provider() -> None:
    """replay_date 提供时禁止实时取数：抛 SnapshotMissingError 且不触达 provider。"""
    SpyProvider.calls.clear()
    set_capability_order("limit_up_pool", ["spy"])

    with pytest.raises(SnapshotMissingError):
        await resolve("limit_up_pool", replay_date=date(2026, 9, 18))

    assert SpyProvider.calls == []


async def test_replay_source_hook_is_used_instead_of_provider() -> None:
    """注入回放读取器后，走读取器而非 provider。"""
    SpyProvider.calls.clear()
    set_capability_order("limit_up_pool", ["spy"])
    seen: list[tuple[str, date]] = []

    async def reader(capability: str, *, replay_date: date, **args: Any) -> list[Any]:
        seen.append((capability, replay_date))
        return []

    set_replay_source(reader)
    result = await resolve("limit_up_pool", replay_date=date(2026, 9, 18))

    assert result == []
    assert seen == [("limit_up_pool", date(2026, 9, 18))]
    assert SpyProvider.calls == []


# ============================================================ 8. 注册表断言


def test_provider_declaring_capability_without_contract_raises() -> None:
    """声明了无契约的能力 → 注册即报错。"""

    class NoContractProvider(BaseProvider):
        source_id = "no_contract"
        label = "非法能力源"
        kind = SourceKind.FAKE
        capabilities = ("capability_without_contract",)
        rate_limit_per_min = 60

        async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
            return {}

    with pytest.raises(RegistryError) as excinfo:
        register_provider(NoContractProvider)
    assert "无契约的能力" in str(excinfo.value)
    assert all(cls.source_id != "no_contract" for cls in all_providers())


def test_capability_order_rejects_unknown_source() -> None:
    """有序源列表引用未注册源 → 报错。"""
    with pytest.raises(RegistryError):
        set_capability_order("limit_up_pool", ["not_registered"])


def test_every_capability_has_contract_and_fake_mapping() -> None:
    """每个已声明能力都有契约，且 fake 源都注册了映射。"""
    assert set(CAPABILITY_CONTRACTS) == set(default_payloads())
    for capability in CAPABILITY_CONTRACTS:
        assert get_mapping("fake", capability) is not None


# ============================================================ 补充：重试 / YAML


async def test_request_with_retry_honours_retry_after() -> None:
    """429 + Retry-After → 按头等待后重试成功。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"code": 1})
        return httpx.Response(200, json={"code": 0})

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await request_with_retry(
            client, "GET", "https://upstream.test/api", source="test", sleep=fake_sleep
        )

    assert response.status_code == 200
    assert calls["n"] == 2
    assert sleeps == [2.0]


async def test_request_with_retry_raises_structured_upstream_error() -> None:
    """持续 5xx → 耗尽尝试后抛 UpstreamError，detail 含尝试次数。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async def fake_sleep(seconds: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamError) as excinfo:
            await request_with_retry(
                client,
                "GET",
                "https://upstream.test/api",
                source="test",
                sleep=fake_sleep,
                max_attempts=2,
            )

    assert excinfo.value.detail["attempts"] == 2
    assert excinfo.value.detail["status"] == 500


def test_yaml_mapping_loaded_and_python_wins(tmp_path: Path) -> None:
    """YAML 映射可加载；与 Python 注册冲突时 Python 优先。"""
    assert get_mapping("demo_http", "daily_bars") is not None  # defs/example.yaml

    (tmp_path / "override.yaml").write_text(
        "source_id: fake\n"
        "capability: daily_bars\n"
        "record_path: result.rows\n"
        "notes: yaml-should-lose\n"
        "fields:\n"
        "  - target: code\n"
        "    source: symbol\n"
        "    transform: normalize_code\n",
        encoding="utf-8",
    )
    load_yaml_mappings(tmp_path)
    assert get_mapping("fake", "daily_bars").notes != "yaml-should-lose"


async def test_resolve_raw_returns_vendor_payload() -> None:
    """resolve_raw 返回原始 payload（供采集留档），不做映射。"""
    raw = await resolve_raw("daily_bars")
    assert raw.source_id == "fake"
    assert "symbol" in raw.payload["result"]["rows"][0]


async def test_all_capabilities_resolve_to_contracts() -> None:
    """Phase-1 全部能力均可经统一入口解析为契约对象。"""
    # minute_bars / opening_match / auction_series 为按票取数能力、
    # monitor_stocks / monitor_unusual 的 trade_date 取自 args——均为
    # 必需调用参数，无法无参解析（按参数能力的端到端覆盖见各自专项测试）。
    skip = {
        "minute_bars",
        "opening_match",
        "auction_series",
        "monitor_stocks",
        "monitor_unusual",
    }
    for capability, model in CAPABILITY_CONTRACTS.items():
        if capability in skip:
            continue
        rows = await resolve(capability)
        assert rows, f"{capability} 未产出记录"
        assert all(isinstance(row, model) for row in rows)
