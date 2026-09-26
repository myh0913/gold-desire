"""情绪周期历史装配：滚动分位阈值 + 长熊开关 + 剔 ST（T-0008）。

三条修订（用户 2026-09-24 拍板方案 A，口径对齐 emotion-cycle t6/t7）：

1. **固定阈值 → 滚动 120 交易日分位**：分歧晋级线 / 退潮晋级线 / 加速高度线 /
   冰点涨停线，锚点为历史序列的 P15.2 / P73.7 / P62.6 / P10.3（t6 ``mark_dyn``
   的分位语义）；不足 60 日历史回退固定阈值（生产不能因历史短而标 ``None``）。
2. **长熊开关**（t7 ``run_switch`` 照抄）：连续 5 日「最高连板均值<8 且 涨停数
   均值<60」开启，连续 10 日不满足解除；开启时修复/加速态追高腿禁买（接线见
   ``app.strategies.registry``）。
3. **宇宙剔 ST**：判定口径（分位窗口 + 当日值）全部从池行数**现算并剔 ST**；
   开关口径忠实 t7 用**含 ST** 的原始计数——两套口径并存是设计决定。

窗口口径：阈值窗口**不含当日**（对齐 t6 ``out[max(0,i-w):i]``）；开关窗口
**含当日**（对齐 t7）。历史序列每次**完整重算**（原始表现场），不读
``cycle_judgements`` 里存的历史 payload——落库 payload 是给人看的快照，
不是可复算的源数据。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from app.models.market import LimitUpPool
from app.services.cycle import DEFAULT_THRESHOLDS, CycleIndicators, CycleThresholds

__all__ = [
    "BEAR_SWITCH_LIMIT_UP",
    "BEAR_SWITCH_MAX_BOARDS",
    "HISTORY_DAYS",
    "POOL_TYPES",
    "QUANTILE_MIN_HISTORY",
    "QUANTILE_WINDOW",
    "HistoryDay",
    "bear_switch_state",
    "build_indicators",
    "dynamic_thresholds",
    "rolling_quantile",
]

# ------------------------------------------------------------ 口径常量（t6/t7 对齐）

#: 拉取的情绪历史长度（覆盖 120 日分位窗口 + 开关预热余量）。
HISTORY_DAYS = 250
#: 滚动分位窗口宽度（对齐 t6 ``mark_dyn`` w=120）。
QUANTILE_WINDOW = 120
#: 历史不足此数 → 整体回退固定阈值。
QUANTILE_MIN_HISTORY = 60

#: 长熊开关窗口宽度（对齐 t7 W=120，含当日）。
BEAR_SWITCH_WINDOW = 120
#: 开关腿 1：窗口内最高连板均值 < 此值。
BEAR_SWITCH_MAX_BOARDS = 8
#: 开关腿 2：窗口内涨停数均值 < 此值。
BEAR_SWITCH_LIMIT_UP = 60
#: 连续满足 N 日 → 开启。
BEAR_SWITCH_OPEN_STREAK = 5
#: 连续不满足 N 日 → 解除。
BEAR_SWITCH_CLOSE_STREAK = 10

# 分位锚点（t6 语义映射：晋级率 P15.2 → 分歧晋级线；晋级率 P73.7 → 退潮晋级线；
# 高度 P62.6 → 加速高度线；涨停数 P10.3 → 冰点涨停线）
_ANCHOR_DIVERGENCE_PROMOTION = 0.152
_ANCHOR_RETREAT_PROMOTION = 0.737
_ANCHOR_ACCEL_HEIGHT = 0.626
_ANCHOR_ICE_LIMIT_UP = 0.103

#: 历史装配涉及的池型（一次区间查询拉齐）。
POOL_TYPES = ("limit_up", "yesterday_limit_up", "limit_down", "limit_up_broken")


# ------------------------------------------------------------ 纯计算


def rolling_quantile(sorted_vals: Sequence[float], p: float) -> float:
    """已升序序列的 P``p`` 分位数（线性插值，t6 照抄）。

    ``p`` 取 ``[0, 1]``；单元素序列任意分位均返回该元素。
    """
    pos = p * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _is_st(name: str | None) -> bool:
    """名称含 ``ST`` 即剔除（含 ``*ST``，大小写不敏感）。"""
    return "ST" in (name or "").upper()


def _to_float(value: Any) -> float | None:
    """``Decimal`` / ``int`` / ``float`` → ``float``；``None`` / ``bool`` → ``None``。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


@dataclass(slots=True)
class HistoryDay:
    """单日历史：判定口径指标 + 开关口径原始计数。

    ``lu_raw`` / ``mb_raw`` 是**含 ST** 的涨停池行数 / 最高连板数——开关忠实
    t7 用原始计数；判定口径（``ind`` 内）已剔 ST。两套口径并存是设计决定。
    """

    ind: CycleIndicators
    #: 含 ST 的涨停池行数（开关腿 2）。
    lu_raw: int = 0
    #: 含 ST 的最高连板数（开关腿 1）。
    mb_raw: int = 0


def dynamic_thresholds(hist: Sequence[HistoryDay], base: CycleThresholds) -> CycleThresholds:
    """由**不含当日**的历史序列算滚动分位阈值；不足 60 日回退 ``base``。

    逐字段独立回退：窗口内某字段序列全缺失（如晋级率未采集）→ 仅该阈值回退
    固定值，不拖累其余字段。
    """
    n = len(hist)
    if n < QUANTILE_MIN_HISTORY:
        return base
    window = hist[max(0, n - QUANTILE_WINDOW) :]
    promos = sorted(v for v in (_to_float(d.ind.promotion_rate) for d in window) if v is not None)
    heights = sorted(v for v in (_to_float(d.ind.board_height) for d in window) if v is not None)
    ups = sorted(v for v in (_to_float(d.ind.limit_up_count) for d in window) if v is not None)
    updates: dict[str, Any] = {}
    if promos:
        updates["divergence_promotion"] = rolling_quantile(promos, _ANCHOR_DIVERGENCE_PROMOTION)
        updates["retreat_promotion_max"] = rolling_quantile(promos, _ANCHOR_RETREAT_PROMOTION)
    if heights:
        updates["accel_height"] = rolling_quantile(heights, _ANCHOR_ACCEL_HEIGHT)
    if ups:
        updates["ice_limit_up"] = rolling_quantile(ups, _ANCHOR_ICE_LIMIT_UP)
    return replace(base, **updates) if updates else base


def bear_switch_state(days: Sequence[HistoryDay]) -> bool:
    """长熊开关状态机（t7 ``run_switch`` 照抄），返回走完全序列后的开关态。

    前 ``QUANTILE_MIN_HISTORY - 1`` 日为预热期不计数；此后逐日取**含当日**的
    120 日窗口，两腿（连板均值 / 涨停数均值）同时满足 → 连续 5 日开启；
    不满足 → 连续 10 日解除。
    """
    opened = False
    sat = 0
    unsat = 0
    for i in range(len(days)):
        if i < QUANTILE_MIN_HISTORY - 1:
            continue
        window = days[max(0, i - BEAR_SWITCH_WINDOW + 1) : i + 1]
        boards_mean = sum(d.mb_raw for d in window) / len(window)
        ups_mean = sum(d.lu_raw for d in window) / len(window)
        cold = boards_mean < BEAR_SWITCH_MAX_BOARDS and ups_mean < BEAR_SWITCH_LIMIT_UP
        if cold:
            sat += 1
            unsat = 0
            if sat >= BEAR_SWITCH_OPEN_STREAK:
                opened = True
        else:
            unsat += 1
            sat = 0
            if unsat >= BEAR_SWITCH_CLOSE_STREAK:
                opened = False
    return opened


# ------------------------------------------------------------ 装配（读库）


def _assemble_day(
    trade_date: date,
    pools_by_type: Mapping[str, Sequence[LimitUpPool]],
    temperature: float | None,
) -> HistoryDay:
    """由单日各池成分装配 :class:`HistoryDay`。

    判定口径全部从池行数**现算并剔 ST**（不读 sentiment 的计数列——那是上游
    全市场口径，与剔 ST 后的池口径不一致）；温度仍来自 sentiment。
    """
    ups = list(pools_by_type.get("limit_up", ()))
    ups_clean = [row for row in ups if not _is_st(row.name)]
    downs_clean = [row for row in pools_by_type.get("limit_down", ()) if not _is_st(row.name)]
    broken_clean = [row for row in pools_by_type.get("limit_up_broken", ()) if not _is_st(row.name)]
    yesterday_clean = [
        row for row in pools_by_type.get("yesterday_limit_up", ()) if not _is_st(row.name)
    ]

    limit_up_count = len(ups_clean)
    broken_count = len(broken_clean)
    break_ratio = (
        broken_count / (limit_up_count + broken_count)
        if limit_up_count + broken_count > 0
        else None
    )

    board_height = 0
    leader_code = ""
    leader_name = ""
    leader_limit_down = False
    if ups_clean:
        board_height = max(int(row.continue_days or 0) for row in ups_clean)
        leader = max(
            ups_clean,
            key=lambda row: (
                int(row.continue_days or 0),
                _to_float(row.seal_amount_yuan) or 0.0,
            ),
        )
        leader_code = str(leader.code)
        leader_name = str(leader.name)
        leader_limit_down = any(str(row.code) == leader_code for row in downs_clean)

    promotion_rate: float | None = None
    if yesterday_clean:
        prev_codes = {str(row.code) for row in yesterday_clean}
        cur_codes = {str(row.code) for row in ups_clean}
        promotion_rate = len(prev_codes & cur_codes) / len(prev_codes)

    ind = CycleIndicators(
        trade_date=trade_date,
        temperature=temperature,
        limit_up_count=limit_up_count,
        limit_down_count=len(downs_clean),
        break_ratio=break_ratio,
        promotion_rate=promotion_rate,
        board_height=board_height,
        leader_code=leader_code,
        leader_name=leader_name,
        leader_limit_down=leader_limit_down,
    )
    return HistoryDay(
        ind=ind,
        lu_raw=len(ups),
        mb_raw=max((int(row.continue_days or 0) for row in ups), default=0),
    )


async def _load_history(repos: Any, trade_date: date) -> list[HistoryDay]:
    """拉 ``trade_date``（含）前的情绪 + 池历史，装配为升序 :class:`HistoryDay`。

    池数据**一次区间查询**拉齐（避免逐日逐池循环）；某日池缺失按空池处理
    （计数为 0，不阻断序列）。
    """
    sentiments = await repos.market_sentiment.get_history(HISTORY_DAYS)
    if not sentiments:
        return []
    start = sentiments[0].trade_date
    pools = await repos.limit_up_pool.get_pools_between(start, trade_date, POOL_TYPES)
    by_day: dict[tuple[date, str], list[LimitUpPool]] = {}
    for row in pools:
        by_day.setdefault((row.trade_date, row.pool_type), []).append(row)

    days: list[HistoryDay] = []
    for sent in sentiments:
        if sent.trade_date > trade_date:
            break
        pools_by_type = {
            pool_type: by_day.get((sent.trade_date, pool_type), ()) for pool_type in POOL_TYPES
        }
        days.append(_assemble_day(sent.trade_date, pools_by_type, _to_float(sent.temperature)))
    return days


async def build_indicators(
    repos: Any,
    trade_date: date,
    base: CycleThresholds = DEFAULT_THRESHOLDS,
) -> tuple[CycleIndicators, CycleThresholds]:
    """装配当日指标 + 动态阈值 + 长熊开关（T-0008 主入口）。

    - 当日 sentiment 已入库 → 直接用历史序列末位；未入库（盘中窗口期）→
      单独拉当日池装配（温度 ``None``），开关两腿来自池、不依赖 sentiment，
      故当日仍拼进序列参与开关计算；
    - 阈值分位窗口**不含当日**；开关序列**含当日**。
    """
    rows = await _load_history(repos, trade_date)
    if rows and rows[-1].ind.trade_date == trade_date:
        today = rows[-1].ind
    else:
        pools = await repos.limit_up_pool.get_pools_between(trade_date, trade_date, POOL_TYPES)
        by_type: dict[str, list[LimitUpPool]] = {}
        for row in pools:
            by_type.setdefault(row.pool_type, []).append(row)
        today_day = _assemble_day(trade_date, by_type, None)
        rows = [*rows, today_day]
        today = today_day.ind

    thresholds = dynamic_thresholds(rows[:-1], base)
    today.bear_switch = bear_switch_state(rows)
    return today, thresholds
