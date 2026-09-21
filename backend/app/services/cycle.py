"""情绪周期判定（服务层）。

**口径来源**：照搬参考实现 `quant-system/src/quant_system/cycle.py`——其中阈值集
是用户 2026-09-07 亲自确认的（退潮线=炸板率 35% / 加速=温度 60 + 晋级 25% + 高度 5
三条同时 / 仓位梯度 0.3-0.5-1.0），2026-09-14 又有两处补充（炸板率 ≥35% 需叠加弱势
信号；高晋级 + 高炸板 → 高位分歧不退潮）。本模块**只在数据来源上适配** gold-desire：
指标全部从**库**取（情绪指标 + 涨停池 + 跌停池），不直连上游。

六态取值与 :class:`app.strategies.protocol.CycleState` 完全一致，故直接复用该枚举，
不另立一份。

**与参考实现的一处已知差异**：`index_daily_pct`（上证指数日涨幅）gold-desire 未采集，
恒为 ``None`` → 黑天鹅规则只剩「跌停 ≥ 100」那条腿（`data_degraded` 会标记该缺失）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.strategies.protocol import CycleState

__all__ = [
    "DEFAULT_THRESHOLDS",
    "CycleIndicators",
    "CycleJudgement",
    "CycleThresholds",
    "classify",
]


# ============================================================ 阈值（用户确认区）


@dataclass(frozen=True, slots=True)
class CycleThresholds:
    """全部阈值集中在此（照搬参考实现，用户 2026-09-07 确认）。

    要调参只改这一个类，别处不出现魔法数字。
    """

    # 退潮（用户确认：炸板率 ≥35%）
    retreat_break_ratio: float = 0.35
    retreat_limit_down: int = 50
    retreat_leader_limit_down: bool = True
    retreat_temp_floor: float = 25.0
    retreat_promotion_max: float = 0.25

    # 分歧
    divergence_break_ratio: float = 0.25
    divergence_promotion: float = 0.15

    # 加速/高潮（用户确认：三条同时满足）
    accel_temp: float = 60.0
    accel_promotion: float = 0.25
    accel_height: int = 5

    # 修复
    repair_temp_low: float = 30.0
    repair_temp_high: float = 60.0
    repair_limit_up: int = 40
    repair_break_ratio: float = 0.25

    # 冰点
    ice_temp: float = 30.0
    ice_limit_down: int = 30
    ice_limit_up: int = 30

    # 冰点转折（保留：参考实现中定义，v0 未参与判定分支）
    turn_limit_up: int = 50
    turn_temp_recover: float = 40.0

    # 过热（减半，不直接禁买）
    overheated_temp: float = 75.0
    overheated_height: int = 6

    # 黑天鹅近似
    blackswan_index_drop: float = -0.03
    blackswan_limit_down: int = 100


DEFAULT_THRESHOLDS = CycleThresholds()


# ============================================================ 指标与结果


@dataclass(slots=True)
class CycleIndicators:
    """单日情绪指标（由库中数据装配）。"""

    trade_date: date
    temperature: float | None = None
    limit_up_count: int = 0
    limit_down_count: int = 0
    break_ratio: float | None = None
    promotion_rate: float | None = None
    board_height: int = 0
    leader_code: str = ""
    leader_name: str = ""
    leader_limit_down: bool = False
    index_daily_pct: float | None = None

    def to_payload(self) -> dict[str, Any]:
        """转 JSON 友好字典（落库用）。"""
        return {
            "trade_date": self.trade_date.isoformat(),
            "temperature": self.temperature,
            "limit_up_count": self.limit_up_count,
            "limit_down_count": self.limit_down_count,
            "break_ratio": self.break_ratio,
            "promotion_rate": self.promotion_rate,
            "board_height": self.board_height,
            "leader_code": self.leader_code,
            "leader_name": self.leader_name,
            "leader_limit_down": self.leader_limit_down,
            "index_daily_pct": self.index_daily_pct,
        }


@dataclass(slots=True)
class CycleJudgement:
    """判定结果。"""

    state: CycleState
    reasons: list[str] = field(default_factory=list)
    overheated: bool = False
    relaxed_needs_confirm: bool = False
    data_degraded: bool = False
    indicators: CycleIndicators | None = None


# ============================================================ 判定


def _num(value: Any) -> float | None:
    """``Decimal`` / ``int`` / ``float`` → ``float``；``None`` / 非数 → ``None``。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def classify(
    ind: CycleIndicators,
    th: CycleThresholds = DEFAULT_THRESHOLDS,
    prev_state: CycleState | None = None,
) -> CycleJudgement:
    """六态判定。优先级：退潮 > 分歧 > 冰点 > 加速 > 修复 > 转折（安全优先）。

    逻辑与参考实现逐条对齐（含 2026-09-14 的两处用户补充）。
    """
    j = CycleJudgement(state=CycleState.REPAIR, indicators=ind)
    br = ind.break_ratio
    t = ind.temperature

    # 数据降级标记：关键指标缺失时判定可信度下降（区分「真分歧」与「数据源挂了」）
    missing = [
        label
        for label, value in (
            ("温度", ind.temperature),
            ("炸板率", ind.break_ratio),
            ("晋级率", ind.promotion_rate),
        )
        if value is None
    ]
    if missing:
        j.data_degraded = True
        j.reasons.append(f"⚠️ 数据降级：{'+'.join(missing)}缺失，判定可信度低")

    # 黑天鹅近似 → 直接退潮
    black_swan = (
        ind.index_daily_pct is not None and ind.index_daily_pct <= th.blackswan_index_drop
    ) or ind.limit_down_count >= th.blackswan_limit_down
    if black_swan:
        j.state = CycleState.RETREAT
        drop = ind.index_daily_pct * 100 if ind.index_daily_pct is not None else None
        j.reasons.append(
            f"黑天鹅近似：大盘{drop if drop is not None else '-'}% / 跌停{ind.limit_down_count}"
        )
        return j

    # 退潮（复合条件：炸板率≥35% 需叠加弱势信号；高晋级 + 高炸板 → 分歧不退潮）
    high_break = br is not None and br >= th.retreat_break_ratio
    weak_alongside = (
        ind.promotion_rate is not None and ind.promotion_rate < th.retreat_promotion_max
    ) or ind.limit_down_count >= th.retreat_limit_down
    if (
        (high_break and weak_alongside)
        or ind.limit_down_count >= th.retreat_limit_down
        or (th.retreat_leader_limit_down and ind.leader_limit_down)
        or (t is not None and t < th.retreat_temp_floor)
    ):
        j.state = CycleState.RETREAT
        if high_break and weak_alongside:
            j.reasons.append(
                f"炸板率{br * 100:.1f}%≥{th.retreat_break_ratio * 100:.0f}% 且 晋级率弱/跌停多"
            )
        if ind.limit_down_count >= th.retreat_limit_down:
            j.reasons.append(f"跌停{ind.limit_down_count}≥{th.retreat_limit_down}")
        if ind.leader_limit_down:
            j.reasons.append("龙头跌停")
        if t is not None and t < th.retreat_temp_floor:
            j.reasons.append(f"温度{t:.0f}<{th.retreat_temp_floor:.0f}")
        return j

    # 分歧
    if (br is not None and br >= th.divergence_break_ratio) or (
        ind.promotion_rate is not None and ind.promotion_rate < th.divergence_promotion
    ):
        j.state = CycleState.DIVERGE
        if br is not None and br >= th.retreat_break_ratio:
            promo = ind.promotion_rate
            if promo is not None:
                # 高炸板但晋级强 → 高位分歧加剧（用户确认 2026-09-14：不归退潮）
                j.reasons.append(
                    f"炸板率{br * 100:.1f}%≥{th.retreat_break_ratio * 100:.0f}% 但晋级率"
                    f"{promo * 100:.1f}%≥{th.retreat_promotion_max * 100:.0f}%（高位分歧加剧）"
                )
            else:
                j.reasons.append(
                    f"炸板率{br * 100:.1f}%≥{th.retreat_break_ratio * 100:.0f}%（高位分歧）"
                )
        elif br is not None and br >= th.divergence_break_ratio:
            j.reasons.append(
                f"炸板率{br * 100:.1f}%∈"
                f"[{th.divergence_break_ratio * 100:.0f}%,{th.retreat_break_ratio * 100:.0f}%)"
            )
        if ind.promotion_rate is not None and ind.promotion_rate < th.divergence_promotion:
            j.reasons.append(
                f"晋级率{ind.promotion_rate * 100:.1f}%<{th.divergence_promotion * 100:.0f}%"
            )
        return j

    # 冰点
    if (
        t is not None
        and t < th.ice_temp
        and (ind.limit_down_count >= th.ice_limit_down or ind.limit_up_count < th.ice_limit_up)
    ):
        j.state = CycleState.ICE
        j.reasons.append(
            f"温度{t:.0f}<{th.ice_temp:.0f} 且 跌停{ind.limit_down_count}/涨停{ind.limit_up_count}"
        )
        return j

    # 加速（三条同时，用户确认）
    if (
        t is not None
        and t >= th.accel_temp
        and ind.promotion_rate is not None
        and ind.promotion_rate >= th.accel_promotion
        and ind.board_height >= th.accel_height
    ):
        j.state = CycleState.ACCEL
        j.reasons.append(
            f"温度{t:.0f}≥{th.accel_temp:.0f} + 晋级{ind.promotion_rate * 100:.0f}%≥"
            f"{th.accel_promotion * 100:.0f}% + 高度{ind.board_height}≥{th.accel_height}"
        )
    elif (
        t is not None
        and th.repair_temp_low <= t <= th.repair_temp_high
        and ind.limit_up_count >= th.repair_limit_up
        and (br is not None and br < th.repair_break_ratio)
    ):
        j.state = CycleState.REPAIR
        j.reasons.append(
            f"温度{t:.0f}∈[{th.repair_temp_low:.0f},{th.repair_temp_high:.0f}] "
            f"涨停{ind.limit_up_count}≥{th.repair_limit_up} "
            f"炸板率{br * 100:.1f}%<{th.repair_break_ratio * 100:.0f}%"
        )
    else:
        # 未达修复标准 → 归入分歧（结构不达标的中性日视为弱）
        j.state = CycleState.DIVERGE
        j.reasons.append("未达修复/加速标准（结构偏弱归分歧）")

    # 过热标记
    if t is not None and t >= th.overheated_temp and ind.board_height >= th.overheated_height:
        j.overheated = True
        j.reasons.append(
            f"过热：温度{t:.0f}≥{th.overheated_temp:.0f} 且 高度{ind.board_height}≥"
            f"{th.overheated_height} → 仓位减半"
        )

    # 放宽确认标记（修复/加速首次出现 → 建议次日复认）
    if j.state in (CycleState.REPAIR, CycleState.ACCEL) and prev_state != j.state:
        j.relaxed_needs_confirm = True
    return j


def now_utc() -> datetime:
    """当前时间（UTC，带时区）。"""
    return datetime.now(UTC)


# ============================================================ 指标装配（读库）


async def build_indicators(repos: Any, trade_date: date) -> CycleIndicators:
    """从**库**装配单日指标（情绪指标 + 涨停池 + 跌停池），不直连上游。

    口径对照参考实现 `compute_indicators`：

    - 温度 / 涨跌停家数 / 炸板率：``market_sentiment``（其中炸板率用
      ``炸板 / (炸板 + 涨停)`` 由计数**现算**，与参考实现一致）；
    - 高度 / 龙头：当日 ``limit_up`` 池按连板天数取最高（并列时封单额大者优先）；
    - 晋级率：``yesterday_limit_up`` 池 ∩ ``limit_up`` 池 / ``yesterday_limit_up`` 池。
      该池即「昨日涨停」在本日的归档池，与参考实现的「上一交易日涨停池」同义；
    - 龙头是否跌停：龙头 code 是否出现在当日 ``limit_down`` 池；
    - 指数日涨幅：**gold-desire 未采集 → 恒为 None**（黑天鹅规则只剩跌停腿，
      由 ``data_degraded`` 之外的规则差异体现）。
    """
    ind = CycleIndicators(trade_date=trade_date)

    sentiment = await repos.market_sentiment.get(trade_date)
    if sentiment is not None:
        ind.temperature = _num(sentiment.temperature)
        ind.limit_up_count = int(sentiment.limit_up_count or 0)
        ind.limit_down_count = int(sentiment.limit_down_count or 0)
        broken = int(sentiment.broken_board_count or 0)
        if ind.limit_up_count + broken > 0:
            ind.break_ratio = broken / (ind.limit_up_count + broken)

    today = await repos.limit_up_pool.get_pool(trade_date, "limit_up")
    yesterday = await repos.limit_up_pool.get_pool(trade_date, "yesterday_limit_up")

    if today:
        ind.board_height = max(int(row.continue_days or 0) for row in today)
        leader = max(
            today,
            key=lambda row: (
                int(row.continue_days or 0),
                _num(row.seal_amount_yuan) or 0.0,
            ),
        )
        ind.leader_code = str(leader.code)
        ind.leader_name = str(leader.name)

    if yesterday:
        prev_codes = {str(row.code) for row in yesterday}
        cur_codes = {str(row.code) for row in today}
        if prev_codes:
            ind.promotion_rate = len(prev_codes & cur_codes) / len(prev_codes)

    if ind.leader_code:
        downs = await repos.limit_up_pool.get_pool(trade_date, "limit_down")
        ind.leader_limit_down = any(str(row.code) == ind.leader_code for row in downs)

    return ind


def _position_factor(state: CycleState) -> float | None:
    """按**各策略自声明**的门控矩阵取最保守的仓位因子（无策略则 ``None``）。

    参考实现用一张硬编码的「策略名 → 仓位系数」表；gold-desire 的架构要求门控
    由**策略自己声明**（core 不维护以策略名为 key 的硬编码矩阵，见
    ``docs/extend-strategy.md``）。此处因此改为询问已注册策略的声明矩阵，取
    最小值——即「所有策略都被放行时能开的最大仓位」，等价于参考实现里那个总开关。
    """
    try:
        from app.strategies.registry import all_strategies, evaluate_gate
    except Exception:  # pragma: no cover - 策略包不可用时静默降级
        return None
    factors: list[float] = []
    for cls in all_strategies():
        try:
            decision = evaluate_gate(cls, state)
        except Exception:  # pragma: no cover - 单个策略声明异常不影响判定
            continue
        factor = _num(getattr(decision, "position_factor", None))
        if factor is not None:
            factors.append(factor)
    return min(factors) if factors else None


# ============================================================ 服务


class CycleService:
    """情绪周期判定服务：装配指标 → 判定 → 落库 / 读取。"""

    def __init__(self, repos: Any, thresholds: CycleThresholds = DEFAULT_THRESHOLDS) -> None:
        self._repos = repos
        self._th = thresholds

    async def judge(self, trade_date: date) -> CycleJudgement:
        """判定并**幂等落库**（同日重跑只留最新一次），返回判定结果。"""
        indicators = await build_indicators(self._repos, trade_date)
        prev_state = await self._repos.cycle_judgements.previous_state(trade_date)
        prev = CycleState(prev_state) if prev_state in CycleState._value2member_map_ else None
        judgement = classify(indicators, self._th, prev)
        state = judgement.state
        await self._repos.cycle_judgements.upsert_one(
            {
                "trade_date": trade_date,
                "state": state.value,
                "reasons": list(judgement.reasons),
                "indicators": indicators.to_payload(),
                "overheated": judgement.overheated,
                "relaxed_needs_confirm": judgement.relaxed_needs_confirm,
                "data_degraded": judgement.data_degraded,
                "position_factor": _position_factor(state),
                "ran_at": now_utc(),
                "source": "derived",
            }
        )
        return judgement
