"""因子注册表 / 配置中心测试（SQLite 内存库，无需 PostgreSQL 与 Redis）。

覆盖：注册表与 schema 导出、档位边界、参数解析顺序、并发覆盖隔离、
缓存命中与版本失效、有效性统计（含 A/B/C 分段）、阈值来自参数。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import date

import pytest
from app.core.cache import MemoryCache
from app.db.base import Base
from app.factors import (
    BaseFactor,
    FactorContext,
    FactorParamSpec,
    FactorResult,
    FactorResultCache,
    FactorSample,
    all_factors,
    effectiveness,
    export_schemas,
    factor_cache_key,
    get_factor,
    get_params,
    override_params,
    register_factor,
    sync_definitions,
)
from app.factors.base import FactorRegistryError
from app.factors.registry import current_overrides
from app.repositories import Repositories
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

#: 首批内置因子（Task 8 要求实现的全部 factor_id）。
BUILTIN_IDS = {
    "first_yin_amplitude",
    "first_yin_shape",
    "next_day_vol_vs_first_yin",
    "vol_vs_wave_peak",
    "vol_vs_prev",
    "vol_vs_wave_mean",
    "next_day_open_pct",
    "first_yin_open_pct",
    "next_next_day_open_pct",
    "continue_boards",
    "wave_vol_trend",
    "first_yin_low_time",
    "first_yin_close_pos",
    "wave_one_word_count",
    "next_day_close_pct",
}


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 会话；每个用例独立建库以保证隔离。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


def _ctx(**metrics: object) -> FactorContext:
    """构造带指定派生指标的上下文。"""
    return FactorContext(
        code="000001",
        trade_date=date(2026, 9, 18),
        metrics=dict(metrics),
    )


def _bucket_of(factor_id: str, value: object, params: dict[str, object] | None = None) -> str:
    """用给定取值判定档位。"""
    factor = get_factor(factor_id)()
    return factor.classify(value, params if params is not None else factor.default_params())


# ============================================================ 1. 注册表 / schema


def test_all_builtin_factors_registered() -> None:
    """首批因子全部注册，且 get_factor 可取回。"""
    registered = {cls.factor_id for cls in all_factors()}
    assert registered >= BUILTIN_IDS
    for factor_id in BUILTIN_IDS:
        assert get_factor(factor_id).factor_id == factor_id
    with pytest.raises(FactorRegistryError):
        get_factor("no_such_factor")


def test_duplicate_factor_id_raises() -> None:
    """重复 factor_id 在注册（导入）时即报错。"""

    class _Duplicate(BaseFactor):
        factor_id = "first_yin_amplitude"
        label = "重复"
        category = "test"
        description = "重复标识"

        def compute(self, ctx: FactorContext, params: dict[str, object]) -> FactorResult:
            return self.result(0.0, params)

    with pytest.raises(FactorRegistryError):
        register_factor(_Duplicate)

    class _NoId(BaseFactor):
        label = "无标识"
        category = "test"

        def compute(self, ctx: FactorContext, params: dict[str, object]) -> FactorResult:
            return self.result(0.0, params)

    with pytest.raises(FactorRegistryError):
        register_factor(_NoId)


def test_export_schemas_is_frontend_renderable() -> None:
    """export_schemas 输出含全部因子的参数 schema，字段齐备可渲染表单。"""
    schemas = export_schemas()
    assert len(schemas) >= len(BUILTIN_IDS)
    required = {"key", "label", "type", "default", "min", "max", "step", "unit", "description"}
    seen = set()
    for schema in schemas:
        assert {"factor_id", "label", "category", "description", "params", "buckets"} <= set(schema)
        assert schema["params"], f"{schema['factor_id']} 无参数 schema"
        assert schema["buckets"], f"{schema['factor_id']} 无档位定义"
        for spec in schema["params"]:
            assert required <= set(spec)
            assert spec["type"] in {"float", "int", "bool", "percent", "enum"}
        seen.add(schema["factor_id"])
    assert seen >= BUILTIN_IDS
    amplitude = next(s for s in schemas if s["factor_id"] == "first_yin_amplitude")
    spec = next(p for p in amplitude["params"] if p["key"] == "min_amplitude")
    assert spec["default"] == 0.08


async def test_sync_definitions_upserts_and_is_idempotent(session: AsyncSession) -> None:
    """sync_definitions 把全部因子幂等写入 factor_defs。"""
    repos = Repositories.build(session)
    synced = await sync_definitions(repos)
    await session.commit()
    assert synced == sorted(synced)
    assert set(synced) == {cls.factor_id for cls in all_factors()}

    rows = await repos.factor_defs.list_all()
    assert {row.factor_id for row in rows} == set(synced)
    stored = next(row for row in rows if row.factor_id == "first_yin_amplitude")
    assert {spec["key"] for spec in stored.params_schema["params"]} >= {"min_amplitude"}

    await sync_definitions(repos)
    await session.commit()
    assert len(await repos.factor_defs.list_all()) == len(synced)


# ============================================================ 2. 档位边界


def test_bucket_boundaries() -> None:
    """各因子在边界值上落入预期档位。"""
    assert _bucket_of("first_yin_amplitude", 0.08) == ">=8%"
    assert _bucket_of("first_yin_amplitude", 0.05) == "<=5%"
    assert _bucket_of("first_yin_amplitude", 0.06) == "5~8%"

    assert _bucket_of("next_day_vol_vs_first_yin", 0.6) == "0.6~1.0"
    assert _bucket_of("next_day_vol_vs_first_yin", 0.59) == "<0.6"
    assert _bucket_of("next_day_vol_vs_first_yin", 1.0) == "1.0~1.5"
    assert _bucket_of("next_day_vol_vs_first_yin", 1.5) == ">=1.5"

    assert _bucket_of("first_yin_low_time", 90) == ">=90"
    assert _bucket_of("first_yin_low_time", 89) == "30~90"
    assert _bucket_of("first_yin_low_time", 30) == "30~90"
    assert _bucket_of("first_yin_low_time", 29) == "<30"

    assert _bucket_of("next_day_open_pct", -0.03) == "<=-3%"
    assert _bucket_of("next_day_open_pct", -0.01) == "-3~0%"
    assert _bucket_of("next_day_open_pct", 0.0) == "0~+3%"
    assert _bucket_of("next_day_open_pct", 0.03) == ">=+3%"

    assert _bucket_of("first_yin_open_pct", 0.0) == "0~+3%"
    assert _bucket_of("first_yin_open_pct", -0.01) == "<0%"

    assert _bucket_of("continue_boards", 2) == "2"
    assert _bucket_of("continue_boards", 3) == ">=3"
    assert _bucket_of("continue_boards", 4) == ">=4"

    assert _bucket_of("wave_vol_trend", 1.05) == ">=1.05"
    assert _bucket_of("wave_vol_trend", 0.7) == "0.7~1.05"
    assert _bucket_of("wave_vol_trend", 0.69) == "<0.7"

    assert _bucket_of("first_yin_close_pos", 0.3) == "<=0.3"
    assert _bucket_of("first_yin_close_pos", 0.6) == "0.3~0.6"
    assert _bucket_of("first_yin_close_pos", 0.61) == ">0.6"

    assert _bucket_of("wave_one_word_count", 0) == "==0"
    assert _bucket_of("wave_one_word_count", 1) == ">=1"

    assert _bucket_of("next_day_close_pct", 0.0) == ">=0%"
    assert _bucket_of("next_day_close_pct", -0.05) == "<=-5%"
    assert _bucket_of("next_day_close_pct", -0.02) == "-5~0%"

    assert _bucket_of("first_yin_shape", "单边下跌") == "单边下跌"
    assert _bucket_of("first_yin_shape", "尾盘跳水") == "尾盘跳水"
    assert _bucket_of("first_yin_shape", "未知形态") == "其他"


def test_compute_reads_metrics_and_marks_missing_degraded() -> None:
    """compute 从 ctx.metrics 取值；缺数据时降级且档位为「数据缺失」。"""
    factor = get_factor("first_yin_amplitude")()
    result = factor.compute(_ctx(d_amp_pct=0.09), factor.default_params())
    assert result.value == pytest.approx(0.09)
    assert result.bucket == ">=8%"
    assert result.degraded is False
    assert result.detail["d_amp_pct"] == pytest.approx(0.09)

    empty = factor.compute(FactorContext(code="000001", trade_date=date(2026, 9, 18)), {})
    assert empty.degraded is True
    assert empty.value is None
    assert empty.bucket == "数据缺失"


# ============================================================ 3. 参数解析顺序


async def test_params_resolution_order_and_invalid_fallback(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """解析顺序：覆盖 > active 配置 > 代码默认；非法值回退默认并告警。"""
    repos = Repositories.build(session)
    draft = await repos.factor_configs.create_draft("first_yin_amplitude", {"min_amplitude": 0.09})
    await repos.factor_configs.activate("first_yin_amplitude", int(draft.version))
    await session.commit()

    # 代码默认
    assert (await get_params("first_yin_amplitude"))["min_amplitude"] == pytest.approx(0.08)
    # active 配置覆盖代码默认
    active = await get_params("first_yin_amplitude", repos)
    assert active["min_amplitude"] == pytest.approx(0.09)
    assert active["low_amplitude"] == pytest.approx(0.05)
    # 运行时覆盖优先于 active 配置
    with override_params("first_yin_amplitude", {"min_amplitude": 0.07}):
        assert (await get_params("first_yin_amplitude", repos))["min_amplitude"] == pytest.approx(
            0.07
        )
    # 退出覆盖后回到 active
    assert (await get_params("first_yin_amplitude", repos))["min_amplitude"] == pytest.approx(0.09)

    # 非法值（超出 max=1.0）→ 回退代码默认 + 结构化告警
    with (
        override_params("first_yin_amplitude", {"min_amplitude": 5.0}),
        caplog.at_level(logging.WARNING, logger="app.factors.registry"),
    ):
        invalid = await get_params("first_yin_amplitude", repos)
    assert invalid["min_amplitude"] == pytest.approx(0.08)
    warnings = [record for record in caplog.records if record.msg == "factor_param_invalid"]
    assert warnings, "非法参数应发出结构化告警"
    assert warnings[0].factor_id == "first_yin_amplitude"  # type: ignore[attr-defined]
    assert warnings[0].key == "min_amplitude"  # type: ignore[attr-defined]
    assert warnings[0].fallback == 0.08  # type: ignore[attr-defined]

    # 类型非法同样回退
    with override_params("first_yin_amplitude", {"min_amplitude": "oops"}):
        typed = await get_params("first_yin_amplitude", repos)
    assert typed["min_amplitude"] == pytest.approx(0.08)


# ============================================================ 4. 覆盖隔离


async def test_override_isolation_across_concurrent_tasks() -> None:
    """并发任务各自的覆盖互不可见（ContextVar 隔离，回归旧全局 dict 缺陷）。"""
    results: dict[str, float] = {}

    async def worker(name: str, value: float) -> None:
        with override_params("first_yin_amplitude", {"min_amplitude": value}):
            await asyncio.sleep(0)  # 让出控制权，制造交错
            seen = (await get_params("first_yin_amplitude"))["min_amplitude"]
            results[name] = seen
            await asyncio.sleep(0)
            again = (await get_params("first_yin_amplitude"))["min_amplitude"]
            assert again == pytest.approx(value)

    await asyncio.gather(worker("a", 0.07), worker("b", 0.09))

    assert results == {"a": pytest.approx(0.07), "b": pytest.approx(0.09)}
    # 主上下文不受子任务影响
    assert current_overrides() == {}
    assert (await get_params("first_yin_amplitude"))["min_amplitude"] == pytest.approx(0.08)


# ============================================================ 5. 缓存失效


async def test_cache_hit_and_params_version_invalidation() -> None:
    """同版本命中缓存；参数版本变化则重算。"""
    cache = FactorResultCache(MemoryCache())
    ctx = _ctx(d_amp_pct=0.09)
    calls = 0

    def compute(context: FactorContext, params: dict[str, object]) -> FactorResult:
        nonlocal calls
        calls += 1
        return FactorResult(
            factor_id="first_yin_amplitude",
            value=0.09,
            bucket=">=8%",
            detail={"d_amp_pct": 0.09},
        )

    first = await cache.get_or_compute(
        "first_yin_amplitude", ctx, {}, params_version="v1", compute=compute
    )
    second = await cache.get_or_compute(
        "first_yin_amplitude", ctx, {}, params_version="v1", compute=compute
    )
    assert calls == 1
    assert first == second

    # 参数版本变化 → 键变化 → 重新计算
    await cache.get_or_compute("first_yin_amplitude", ctx, {}, params_version="v2", compute=compute)
    assert calls == 2

    # 换日期 / 换代码同样失效
    other = FactorContext(code="000002", trade_date=date(2026, 9, 18), metrics={"d_amp_pct": 0.09})
    await cache.get_or_compute(
        "first_yin_amplitude", other, {}, params_version="v1", compute=compute
    )
    assert calls == 3

    # 键组成：factor:<id>:v<版本>:<日期>:<代码>
    assert factor_cache_key("first_yin_amplitude", "v1", date(2026, 9, 18), "000001") == (
        "factor:first_yin_amplitude:v1:2026-09-18:000001"
    )
    assert await cache.invalidate("first_yin_amplitude") == 3


# ============================================================ 6. 有效性统计


def test_effectiveness_per_bucket_and_segments() -> None:
    """按档位输出 n/期望/胜率，并给出互不重叠、合计等于总量的 A/B/C 分段。"""
    days = [date(2026, 1, day) for day in range(1, 10)]  # 9 个交易日 → 三段各 3 天
    samples = [
        # A 段（days[0:3]）：振幅 ≥8%
        FactorSample("000001", days[0], 0.09, 0.01),
        FactorSample("000002", days[1], 0.09, 0.02),
        FactorSample("000003", days[2], 0.09, -0.01),
        # B 段（days[3:6]）：振幅 5~8%
        FactorSample("000004", days[3], 0.06, 0.03),
        FactorSample("000005", days[4], 0.06, 0.04),
        FactorSample("000006", days[5], 0.06, 0.05),
        # C 段（days[6:9]）：振幅 ≥8%
        FactorSample("000007", days[6], 0.09, 0.06),
        FactorSample("000008", days[7], 0.09, -0.02),
        FactorSample("000009", days[8], 0.09, 0.07),
    ]

    report = effectiveness("first_yin_amplitude", samples)
    by_bucket = {item.bucket: item for item in report}
    assert set(by_bucket) == {">=8%", "5~8%"}

    high = by_bucket[">=8%"]
    assert high.n == 6
    assert high.mean_return == pytest.approx(0.13 / 6)
    assert high.win_rate == pytest.approx(4 / 6)
    # 分段齐备且互不重叠（n 之和等于总量）
    assert set(high.segments) == {"A", "B", "C"}
    assert sum(seg.n for seg in high.segments.values()) == high.n
    assert high.segments["A"].n == 3
    assert high.segments["A"].mean_return == pytest.approx(0.02 / 3)
    assert high.segments["B"].n == 0
    assert high.segments["C"].n == 3
    assert high.segments["C"].mean_return == pytest.approx(0.11 / 3)

    mid = by_bucket["5~8%"]
    assert mid.n == 3
    assert mid.mean_return == pytest.approx(0.04)
    assert mid.win_rate == pytest.approx(1.0)
    assert mid.segments["B"].n == 3
    assert mid.segments["A"].n == 0
    assert sum(seg.n for seg in mid.segments.values()) == mid.n

    # 显式切点（readme §1.5 快照）同样可用
    snapshot = effectiveness(
        "first_yin_amplitude", samples, cuts=(date(2026, 1, 4), date(2026, 1, 7))
    )
    assert sum(item.n for item in snapshot) == len(samples)


# ============================================================ 7. 阈值来自参数


def test_thresholds_come_from_params() -> None:
    """非默认阈值应改变档位判定与档位标签（无硬编码常量）。"""
    factor = get_factor("first_yin_amplitude")()
    default_params = factor.default_params()
    tuned_params = {**default_params, "min_amplitude": 0.07}

    assert factor.classify(0.07, default_params) == "5~8%"
    assert factor.classify(0.07, tuned_params) == ">=7%"

    ctx = _ctx(d_amp_pct=0.07)
    assert factor.compute(ctx, default_params).bucket == "5~8%"
    assert factor.compute(ctx, tuned_params).bucket == ">=7%"

    assert [bucket.label for bucket in factor.buckets(default_params)][-1] == ">=8%"
    assert [bucket.label for bucket in factor.buckets(tuned_params)][-1] == ">=7%"

    # 其余因子同样通过 schema 暴露阈值（而非代码常量）
    for cls in all_factors():
        specs: tuple[FactorParamSpec, ...] = cls.params_schema
        assert specs, f"{cls.factor_id} 未暴露任何可配置参数"
