"""情绪周期判定口径测试（纯函数 + 读库装配，零网络）。

口径照搬参考实现 `quant-system/src/quant_system/cycle.py`（用户 2026-09-07 确认 +
2026-09-14 两处补充）。逐条锁住用户确认过的规则，防止后续改动悄悄偏离。

- **退潮线** = 炸板率 35%，但 2026-09-14 补充：炸板率 ≥35% **需叠加弱势信号**
  （晋级率 <25% 或跌停 ≥50）才退潮；高炸板 + 高晋级 → 归**分歧**（高位分歧加剧）。
- **加速** = 温度 ≥60 **且** 晋级率 ≥25% **且** 高度 ≥5（三条同时）。
- **仓位梯度** 0.3-0.5-1.0 由**策略自声明**的门控矩阵决定（core 不硬编码）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from app.db.base import Base
from app.models.market import CycleJudgement, LimitUpPool, MarketSentiment
from app.repositories import Repositories
from app.services.cycle import (
    CycleIndicators,
    CycleService,
    build_indicators,
    classify,
)
from app.strategies.protocol import CycleState
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

DAY = date(2026, 9, 21)


def _ind(**overrides: object) -> CycleIndicators:
    """默认放一个「修复」形态的指标，逐例覆盖单个字段。"""
    base = {
        "temperature": 45.0,
        "limit_up_count": 60,
        "limit_down_count": 5,
        "break_ratio": 0.10,
        "promotion_rate": 0.30,
        "board_height": 4,
    }
    base.update(overrides)
    return CycleIndicators(trade_date=DAY, **base)  # type: ignore[arg-type]


# ============================================================ 退潮（优先级最高）


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"break_ratio": 0.40, "promotion_rate": 0.10}, "炸板率40%≥35% 且 晋级率10%<25%"),
        ({"break_ratio": 0.40, "limit_down_count": 55}, "炸板率40%≥35% 且 跌停55≥50"),
        ({"limit_down_count": 50}, "跌停50≥50"),
        ({"leader_limit_down": True}, "龙头跌停"),
        ({"temperature": 20.0}, "温度20<25"),
        ({"limit_down_count": 100}, "黑天鹅：跌停100≥100"),
    ],
)
def test_retreat_rules(overrides: dict[str, object], why: str) -> None:
    assert classify(_ind(**overrides)).state is CycleState.RETREAT, why


def test_high_break_with_strong_promotion_is_divergence_not_retreat() -> None:
    """用户 2026-09-14 确认：高炸板 + 高晋级 → 高位分歧加剧，**不归退潮**。"""
    j = classify(_ind(break_ratio=0.40, promotion_rate=0.30))

    assert j.state is CycleState.DIVERGE
    assert any("高位分歧加剧" in reason for reason in j.reasons)


# ============================================================ 分歧


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"break_ratio": 0.28}, "炸板率28%∈[25%,35%)"),
        ({"promotion_rate": 0.10}, "晋级率10%<15%"),
    ],
)
def test_divergence_rules(overrides: dict[str, object], why: str) -> None:
    assert classify(_ind(**overrides)).state is CycleState.DIVERGE, why


# ============================================================ 冰点


def test_ice_when_cold_and_heavy_limit_down() -> None:
    j = classify(_ind(temperature=25.0, limit_down_count=35, break_ratio=0.10, promotion_rate=0.3))
    assert j.state is CycleState.ICE


def test_ice_requires_both_conditions() -> None:
    """只有温度低、跌停不多也不算冰点（会落到「未达修复标准」→ 分歧）。"""
    j = classify(_ind(temperature=25.0, limit_down_count=5, limit_up_count=60, promotion_rate=0.3))
    assert j.state is CycleState.DIVERGE


# ============================================================ 加速（三条同时）


def test_accel_requires_all_three_conditions() -> None:
    j = classify(_ind(temperature=73.0, promotion_rate=0.27, board_height=5))
    assert j.state is CycleState.ACCEL


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"temperature": 59.0}, "温度59<60"),
        ({"promotion_rate": 0.24}, "晋级率24%<25%"),
        ({"board_height": 4}, "高度4<5"),
    ],
)
def test_accel_needs_all_three(overrides: dict[str, object], why: str) -> None:
    base = {"temperature": 73.0, "promotion_rate": 0.27, "board_height": 5}
    base.update(overrides)
    assert classify(_ind(**base)).state is not CycleState.ACCEL, why


# ============================================================ 修复 / 兜底


def test_repair_rule() -> None:
    j = classify(_ind(temperature=45.0, limit_up_count=60, break_ratio=0.10, promotion_rate=0.30))
    assert j.state is CycleState.REPAIR


def test_neutral_day_falls_back_to_divergence() -> None:
    """结构不达标的中性日视为弱 → 归分歧。"""
    j = classify(_ind(temperature=45.0, limit_up_count=30, break_ratio=0.10, promotion_rate=0.30))
    assert j.state is CycleState.DIVERGE
    assert any("未达修复/加速标准" in reason for reason in j.reasons)


# ============================================================ 过热 / 降级 / 复认


def test_overheated_flag() -> None:
    j = classify(_ind(temperature=78.0, promotion_rate=0.30, board_height=6))
    assert j.overheated is True
    assert any("过热" in reason for reason in j.reasons)


def test_data_degraded_when_key_indicator_missing() -> None:
    j = classify(_ind(temperature=None))
    assert j.data_degraded is True
    assert any("数据降级" in reason for reason in j.reasons)


def test_relaxed_needs_confirm_on_first_accel() -> None:
    """放宽类状态（修复/加速）首次出现 → 建议次日复认；与前态相同则不再提示。"""
    hot = {"temperature": 73.0, "promotion_rate": 0.27, "board_height": 5}
    assert classify(_ind(**hot), prev_state=None).relaxed_needs_confirm is True
    assert classify(_ind(**hot), prev_state=CycleState.DIVERGE).relaxed_needs_confirm is True
    assert classify(_ind(**hot), prev_state=CycleState.ACCEL).relaxed_needs_confirm is False


# ============================================================ 指标装配（读库）


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存 SQLite 会话；每例独立建库以保证隔离。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
        yield opened
    await engine.dispose()


def _pool(code: str, name: str, pool_type: str, continue_days: int, seal: float = 0.0) -> dict:
    return {
        "trade_date": DAY,
        "pool_type": pool_type,
        "code": code,
        "name": name,
        "continue_days": continue_days,
        "seal_amount_yuan": Decimal(str(seal)),
        "source": "test",
    }


async def _seed(session: AsyncSession) -> None:
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
            max_continue_days=5,
            source="test",
        )
    )
    await session.flush()
    repos = Repositories.build(session)
    await repos.limit_up_pool.upsert_many(
        [
            # 今日涨停：甲（5 板，封单小）、乙（3 板）、丙（2 板）
            _pool("000001.SZ", "甲", "limit_up", 5, seal=1.0),
            _pool("000002.SZ", "乙", "limit_up", 3, seal=9.0),
            _pool("000003.SZ", "丙", "limit_up", 2),
            # 昨日涨停归档：甲、乙（→ 晋级率 2/3）
            _pool("000001.SZ", "甲", "yesterday_limit_up", 4),
            _pool("000002.SZ", "乙", "yesterday_limit_up", 2),
            _pool("000009.SZ", "庚", "yesterday_limit_up", 1),
            # 跌停：无龙头
            _pool("000099.SZ", "辛", "limit_down", 0),
        ]
    )
    await session.flush()


async def test_build_indicators_from_db(session: AsyncSession) -> None:
    """炸板率由计数现算、晋级率取交集、高度取最高、龙头取高连板。"""
    await _seed(session)
    repos = Repositories.build(session)

    ind = await build_indicators(repos, DAY)

    assert ind.temperature == pytest.approx(72.5293)
    assert ind.limit_up_count == 103
    assert ind.limit_down_count == 3
    # 炸板率 = 炸板 / (炸板 + 涨停) = 26 / 129
    assert ind.break_ratio == pytest.approx(26 / 129)
    assert ind.board_height == 5
    assert ind.leader_code == "000001.SZ"
    assert ind.leader_name == "甲"
    assert ind.leader_limit_down is False
    # 昨日涨停 3 只中 2 只今日仍涨停
    assert ind.promotion_rate == pytest.approx(2 / 3)
    # 指数日涨幅未采集 → 恒 None
    assert ind.index_daily_pct is None


async def test_judge_persists_and_is_idempotent(session: AsyncSession) -> None:
    """判定落库；同日重跑只留一行（幂等覆盖）。"""
    await _seed(session)
    repos = Repositories.build(session)

    first = await CycleService(repos).judge(DAY)
    assert first.state is CycleState.ACCEL
    await session.flush()

    await CycleService(repos).judge(DAY)
    await session.flush()

    rows = (
        await session.execute(CycleJudgement.__table__.select())  # type: ignore[arg-type]
    ).all()
    assert len(rows) == 1

    row = await repos.cycle_judgements.get(DAY)
    assert row is not None
    assert row.state == CycleState.ACCEL.value
    assert row.source == "derived"
    assert isinstance(row.ran_at, datetime)
    assert row.indicators["board_height"] == 5


async def test_previous_state_feeds_relaxed_confirm(session: AsyncSession) -> None:
    """前态读取：昨日为分歧、今日加速 → 需要复认。"""
    await _seed(session)
    repos = Repositories.build(session)
    await repos.cycle_judgements.upsert_one(
        {
            "trade_date": date(2026, 9, 18),
            "state": CycleState.DIVERGE.value,
            "reasons": [],
            "indicators": {},
            "overheated": False,
            "relaxed_needs_confirm": False,
            "data_degraded": False,
            "position_factor": None,
            "ran_at": datetime.now(UTC),
            "source": "derived",
        }
    )
    await session.flush()

    assert await repos.cycle_judgements.previous_state(DAY) == CycleState.DIVERGE.value
    judgement = await CycleService(repos).judge(DAY)
    assert judgement.relaxed_needs_confirm is True
