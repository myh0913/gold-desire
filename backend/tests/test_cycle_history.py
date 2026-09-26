"""滚动分位阈值 + 长熊开关 + 剔 ST 宇宙（T-0008）口径测试。

三条修订（用户 2026-09-24 拍板方案 A，回写 gold-desire 生产）：

1. **固定阈值 → 滚动 120 交易日分位**：分歧晋级线 / 退潮晋级线 / 加速高度线 /
   冰点涨停线，锚点分别为历史序列的 P15.2 / P73.7 / P62.6 / P10.3（对齐
   emotion-cycle t6 ``mark_dyn`` 的分位语义）；不足 60 日历史回退固定阈值。
2. **长熊开关**（对齐 t7 ``run_switch``）：连续 5 日「最高连板均值<8 且 涨停
   数均值<60」开启，连续 10 日不满足解除；开启时修复/加速态追高腿禁买。
3. **宇宙剔 ST**：判定口径（分位窗口 + 当日值）全部从池行数**现算并剔 ST**；
   开关口径忠实 t7 用**含 ST** 的原始计数——两套口径并存是设计决定。

分位数线性插值（t6 照抄）；阈值窗口**不含当日**（对齐 ``out[max(0,i-w):i]``），
开关窗口**含当日**（对齐 t7）。锚点分位值在测试中**硬编码**——实现若偏离
口径，此处先红。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.db.base import Base
from app.models.market import MarketSentiment
from app.repositories import Repositories
from app.services.cycle import (
    DEFAULT_THRESHOLDS,
    CycleIndicators,
    CycleService,
)
from app.services.cycle_history import (
    HistoryDay,
    bear_switch_state,
    build_indicators,
    dynamic_thresholds,
    rolling_quantile,
)
from app.strategies.protocol import CycleState
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

EPOCH = date(2026, 1, 1)
DAY = EPOCH


def _d(i: int) -> date:
    """第 ``i`` 个测试日（连续自然日即可，库查询不校验交易日历）。"""
    return EPOCH + timedelta(days=i)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 会话；每例独立建库以保证隔离。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
        yield opened
    await engine.dispose()


def _pool(
    code: str, name: str, pool_type: str, continue_days: int, seal: float = 0.0, day: date = DAY
) -> dict:
    return {
        "trade_date": day,
        "pool_type": pool_type,
        "code": code,
        "name": name,
        "continue_days": continue_days,
        "seal_amount_yuan": Decimal(str(seal)),
        "source": "test",
    }


def _hist_day(
    i: int,
    *,
    promo: float | None = None,
    height: int | None = None,
    lu: int | None = None,
    lu_raw: int = 0,
    mb_raw: int = 0,
) -> HistoryDay:
    """纯函数测试用的历史日（指标字段缺省即 ``None``，不进分位窗口）。"""
    ind = CycleIndicators(
        trade_date=_d(i),
        promotion_rate=promo,
        board_height=height,  # type: ignore[arg-type]
        limit_up_count=lu,  # type: ignore[arg-type]
    )
    return HistoryDay(ind=ind, lu_raw=lu_raw, mb_raw=mb_raw)


# ============================================================ 分位数（t6 照抄）


def test_rolling_quantile_linear_interpolation() -> None:
    vals = [1.0, 2.0, 3.0, 4.0]
    assert rolling_quantile(vals, 0.5) == pytest.approx(2.5)
    assert rolling_quantile(vals, 0.25) == pytest.approx(1.75)  # pos=0.75 → 1+0.75
    assert rolling_quantile(vals, 0.0) == pytest.approx(1.0)
    assert rolling_quantile(vals, 1.0) == pytest.approx(4.0)
    assert rolling_quantile([7.0], 0.3) == pytest.approx(7.0)  # 单元素


# ============================================================ 动态阈值


def test_dynamic_thresholds_fall_back_below_min_history() -> None:
    """59 日 → 整体回退固定阈值；60 日（含 60）起启用分位。"""
    hist59 = [_hist_day(i, promo=i / 100, height=99, lu=99) for i in range(59)]
    assert dynamic_thresholds(hist59, DEFAULT_THRESHOLDS) == DEFAULT_THRESHOLDS

    hist60 = [_hist_day(i, promo=i / 100, height=99, lu=99) for i in range(60)]
    th60 = dynamic_thresholds(hist60, DEFAULT_THRESHOLDS)
    assert th60 != DEFAULT_THRESHOLDS
    assert th60.accel_height == pytest.approx(99.0)


def test_dynamic_thresholds_expanding_window() -> None:
    """60~120 日用全部历史（expanding）；四个锚点分位逐字段对上。"""
    hist = [_hist_day(i, promo=i / 79, height=i % 10, lu=i) for i in range(80)]
    th = dynamic_thresholds(hist, DEFAULT_THRESHOLDS)

    promos = sorted(i / 79 for i in range(80))
    heights = sorted(i % 10 for i in range(80))
    lus = sorted(float(i) for i in range(80))
    assert th.divergence_promotion == pytest.approx(rolling_quantile(promos, 0.152))
    assert th.retreat_promotion_max == pytest.approx(rolling_quantile(promos, 0.737))
    assert th.accel_height == pytest.approx(rolling_quantile(heights, 0.626))
    assert th.ice_limit_up == pytest.approx(rolling_quantile(lus, 0.103))


def test_dynamic_thresholds_rolling_window_and_field_fallback() -> None:
    """121 日 → 窗口 120（首日滚出）；缺失字段逐字段回退固定值。"""
    hist = [_hist_day(i, height=i) for i in range(121)]
    th = dynamic_thresholds(hist, DEFAULT_THRESHOLDS)

    # 窗口 = hist[1:]（height 1..120）；若误含首日 0，P62.6 会从 75.494 变 76.12
    assert th.accel_height == pytest.approx(rolling_quantile(sorted(range(1, 121)), 0.626))
    # promo / lu 全缺失 → 各自回退 base，不互相拖累
    assert th.divergence_promotion == DEFAULT_THRESHOLDS.divergence_promotion
    assert th.retreat_promotion_max == DEFAULT_THRESHOLDS.retreat_promotion_max
    assert th.ice_limit_up == DEFAULT_THRESHOLDS.ice_limit_up


# ============================================================ 长熊开关（t7 照抄）


def _cold_day(i: int) -> HistoryDay:
    """开关口径冷日：涨停 1 家（<60）、最高 2 板（<8）。"""
    return _hist_day(i, lu=1, height=2, lu_raw=1, mb_raw=2)


def _hot_day(i: int) -> HistoryDay:
    """开关口径热日：涨停 4000 家。

    解除腿要求 120 日窗口均值 ≥60——64 个冷日垫底，热日须极端大才能在
    10 日内拉破阈值，这正是开关「难解」设计的体现。
    """
    return _hist_day(i, lu=4000, height=9, lu_raw=4000, mb_raw=9)


def test_bear_switch_opens_after_5_cold_days() -> None:
    """前 59 日预热不计数；第 60~64 日连续 5 个冷日 → 第 64 日开启。"""
    assert bear_switch_state([_cold_day(i) for i in range(64)]) is True


def test_bear_switch_not_open_with_only_4_cold_days() -> None:
    assert bear_switch_state([_cold_day(i) for i in range(63)]) is False


def test_bear_switch_closes_after_10_hot_days() -> None:
    days = [_cold_day(i) for i in range(64)] + [_hot_day(64 + j) for j in range(10)]
    assert bear_switch_state(days) is False


def test_bear_switch_stays_open_with_only_9_hot_days() -> None:
    days = [_cold_day(i) for i in range(64)] + [_hot_day(64 + j) for j in range(9)]
    assert bear_switch_state(days) is True


# ============================================================ 指标装配（读库，剔 ST）


async def _seed_st_day(session: AsyncSession) -> None:
    """单日 seed：各池混入 ST 票，锁「判定口径剔 ST、计数从池现算」。"""
    session.add(
        MarketSentiment(
            trade_date=DAY,
            temperature=Decimal("72.5293"),
            limit_up_count=103,
            limit_down_count=3,
            broken_board_count=26,
            broken_rate=Decimal("0.2015"),
            up_count=3000,
            down_count=1800,
            premium_rate=Decimal("0.05"),
            max_continue_days=6,
            source="test",
        )
    )
    await session.flush()
    repos = Repositories.build(session)
    await repos.limit_up_pool.upsert_many(
        [
            # 今日涨停：甲（5 板）、乙、丙、ST丁（6 板，应被剔——否则高度/龙头全错）
            _pool("000001.SZ", "甲", "limit_up", 5, seal=1.0),
            _pool("000002.SZ", "乙", "limit_up", 3, seal=9.0),
            _pool("000003.SZ", "丙", "limit_up", 2),
            _pool("000004.SZ", "ST丁", "limit_up", 6, seal=99.0),
            # 昨日涨停归档：甲、乙、庚、ST戊（→ 剔 ST 后晋级率 2/3）
            _pool("000001.SZ", "甲", "yesterday_limit_up", 4),
            _pool("000002.SZ", "乙", "yesterday_limit_up", 2),
            _pool("000009.SZ", "庚", "yesterday_limit_up", 1),
            _pool("000010.SZ", "ST戊", "yesterday_limit_up", 1),
            # 跌停：辛、ST己
            _pool("000099.SZ", "辛", "limit_down", 0),
            _pool("000098.SZ", "ST己", "limit_down", 0),
            # 炸板：壬、ST庚
            _pool("000020.SZ", "壬", "limit_up_broken", 0),
            _pool("000021.SZ", "ST庚", "limit_up_broken", 1),
        ]
    )
    await session.flush()


async def test_build_indicators_excludes_st_and_derives_counts_from_pools(
    session: AsyncSession,
) -> None:
    """判定口径：计数全部从池行数现算（剔 ST），不读 sentiment 的计数。"""
    await _seed_st_day(session)
    repos = Repositories.build(session)

    ind, th = await build_indicators(repos, DAY)

    assert ind.temperature == pytest.approx(72.5293)  # 温度仍来自 sentiment
    assert ind.limit_up_count == 3  # 池剔 ST 行数（甲乙丙），非 sentiment 的 103
    assert ind.limit_down_count == 1  # 辛（ST己被剔）
    assert ind.break_ratio == pytest.approx(1 / 4)  # 壬 1 家 / (3 涨停 + 1 炸板)
    assert ind.board_height == 5  # ST丁 6 板被剔
    assert ind.leader_code == "000001.SZ"
    assert ind.leader_name == "甲"
    assert ind.leader_limit_down is False
    assert ind.promotion_rate == pytest.approx(2 / 3)  # 昨日剔 ST 3 只中 2 只晋级
    assert th == DEFAULT_THRESHOLDS  # 1 日历史 < 60 → 回退固定阈值
    assert ind.bear_switch is False  # 不足预热期，开关不开


async def _seed_streak(session: AsyncSession, days: int, boards: list[int] | None = None) -> None:
    """连续 ``days`` 日：每日 sentiment 温度 45 + limit_up 池 1 行。

    ``boards`` 给每日连板数（默认恒 2 → 每日均为开关口径冷日）。
    """
    session.add_all(
        MarketSentiment(
            trade_date=_d(i),
            temperature=Decimal("45"),
            limit_up_count=1,
            limit_down_count=0,
            broken_board_count=0,
            broken_rate=Decimal("0"),
            up_count=100,
            down_count=100,
            premium_rate=Decimal("0"),
            max_continue_days=boards[i] if boards else 2,
            source="test",
        )
        for i in range(days)
    )
    await session.flush()
    repos = Repositories.build(session)
    await repos.limit_up_pool.upsert_many(
        _pool("000001.SZ", "甲", "limit_up", boards[i] if boards else 2, day=_d(i))
        for i in range(days)
    )
    await session.flush()


async def test_build_indicators_dynamic_thresholds_exclude_today(
    session: AsyncSession,
) -> None:
    """61 日递增高度：分位窗口**不含当日**（P62.6=37.934；误含当日=38.56）。"""
    await _seed_streak(session, 61, boards=[i + 1 for i in range(61)])
    repos = Repositories.build(session)

    ind, th = await build_indicators(repos, _d(60))

    assert ind.board_height == 61  # 当日值照常装配
    assert th.accel_height == pytest.approx(rolling_quantile(sorted(range(1, 61)), 0.626))
    assert th.ice_limit_up == pytest.approx(1.0)  # lu 序列全 1 → P10.3=1.0


async def test_judge_appends_bear_switch_reason_and_payload(session: AsyncSession) -> None:
    """65 个冷日：开关开启 → judge 追加禁买 reason，payload 落 bear_switch。"""
    await _seed_streak(session, 65)
    repos = Repositories.build(session)

    judgement = await CycleService(repos).judge(_d(64))

    # 温度 45 / 涨停 1 家 / 无晋级数据 → 未达修复标准，归分歧
    assert judgement.state is CycleState.DIVERGE
    assert any("长熊开关" in reason for reason in judgement.reasons)

    row = await repos.cycle_judgements.get(_d(64))
    assert row is not None
    assert row.indicators["bear_switch"] is True


# ============================================================ 池区间查询


async def test_get_pools_between_returns_range_sorted(session: AsyncSession) -> None:
    """范围过滤 + 交易日升序、池型升序、连板降序。"""
    repos = Repositories.build(session)
    await repos.limit_up_pool.upsert_many(
        [
            _pool("000001.SZ", "甲", "limit_up", 5, day=_d(0)),
            _pool("000002.SZ", "乙", "limit_up", 3, day=_d(0)),
            _pool("000099.SZ", "辛", "limit_down", 0, day=_d(0)),
            _pool("000003.SZ", "丙", "limit_up", 2, day=_d(1)),
            _pool("000004.SZ", "丁", "limit_up", 9, day=_d(9)),  # 范围外
        ]
    )
    await session.flush()

    rows = await repos.limit_up_pool.get_pools_between(_d(0), _d(1), ("limit_up", "limit_down"))
    assert [(r.trade_date, r.pool_type, r.code) for r in rows] == [
        (_d(0), "limit_down", "000099.SZ"),
        (_d(0), "limit_up", "000001.SZ"),
        (_d(0), "limit_up", "000002.SZ"),
        (_d(1), "limit_up", "000003.SZ"),
    ]
