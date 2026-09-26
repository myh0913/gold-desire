"""龙回头样本**类型定义与纯函数**（无 I/O）：字段契约、形态分类、时间映射。

从 :mod:`app.engine.dragon_samples` 拆出（规范：单文件 ≤500 行）。样本构建的
I/O 编排见 :mod:`app.engine.dragon_samples`（盘后全量）与
:mod:`app.engine.dragon_opening`（09:25 开盘注入）；K 线视图 / 涨停等判定
助手见 :mod:`app.engine.dragon_bars`。

字段契约严格对齐 readme §10（``DragonSample`` 及其嵌套 ``MinuteMetrics`` /
``DayMetrics``），策略 / 卖出规则 / 组合代码路径只依赖本模块类型。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.factors.base import Bar, FactorContext, MinutePoint

__all__ = [
    "DayMetrics",
    "DragonSample",
    "MinuteMetrics",
    "classify_shape",
    "minute_time_label",
]

#: 形态分类默认参数（可覆盖；readme §9 五类形态）。
_EARLY_WINDOW = 30
_LATE_INDEX = 90
_LOW_POS = 0.3
_DIVE_DROP = 0.03
_FLAT_RANGE = 0.03


@dataclass(frozen=True, slots=True)
class MinuteMetrics:
    """分时派生指标（readme §10 ``mp_D`` 对象；``mp_T`` / ``mp_T1`` / ``mp_T2`` 同形）。"""

    n: int = 0
    high_pct: float | None = None
    low_pct: float | None = None
    close_pct: float | None = None
    amp_pct: float | None = None
    low_time_i: int | None = None
    close_pos: float | None = None


@dataclass(frozen=True, slots=True)
class DayMetrics:
    """单日全天字段（readme §10 ``t`` / ``t1`` / ``t2`` 对象）。"""

    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    open_pct: float | None = None
    high_pct: float | None = None
    low_pct: float | None = None
    close_pct: float | None = None
    amp_pct: float | None = None
    volume: float | None = None


@dataclass(frozen=True, slots=True)
class DragonSample:
    """单条龙回头结构样本（字段名对齐 readme §10）。

    ``mp_D`` / ``t`` / ``t1`` / ``t2`` 为嵌套对象（分时派生指标 / 各日全天字段），引用时须写
    全路径（``t.open_pct``、``mp_D.low_time_i``）。``px_*`` 为对应交易日的分钟收盘价序列
    （按分钟序号升序，用于卖出规则撮合与买入价定位）。
    """

    code: str
    name: str
    D: date
    T: date
    T1: date
    T2: date | None = None
    boards: int = 0
    wave_vol_trend: float | None = None
    wave_one_word_cnt: int = 0
    wave_peak_vol: float | None = None
    wave_mean_vol: float | None = None
    wave_last_vol: float | None = None
    wave_first_vol: float | None = None
    d_amp_pct: float | None = None
    d_open_pct: float | None = None
    d_high_pct: float | None = None
    d_low_pct: float | None = None
    d_close_pct: float | None = None
    d_vol: float | None = None
    shape_label: str | None = None
    vol_vs_prev: float | None = None
    vol_vs_wavepeak: float | None = None
    vol_vs_wavemean: float | None = None
    t_vol_vs_d: float | None = None
    is_sanbanzu: bool = False
    pre_close: float | None = None
    mp_D: MinuteMetrics = field(default_factory=MinuteMetrics)
    t: DayMetrics = field(default_factory=DayMetrics)
    t1: DayMetrics = field(default_factory=DayMetrics)
    t2: DayMetrics = field(default_factory=DayMetrics)
    px_D: tuple[float, ...] = ()
    px_T: tuple[float, ...] = ()
    px_T1: tuple[float, ...] = ()
    px_T2: tuple[float, ...] = ()

    # ------------------------------------------------------------ 字段快照 / 上下文

    def metrics(self) -> dict[str, Any]:
        """返回 readme §10 口径的扁平/嵌套指标字典（供 :class:`FactorContext`）。"""
        return {
            "boards": self.boards,
            "wave_vol_trend": self.wave_vol_trend,
            "wave_one_word_cnt": self.wave_one_word_cnt,
            "d_amp_pct": self.d_amp_pct,
            "shape_label": self.shape_label,
            "d_open_pct": self.d_open_pct,
            "d_high_pct": self.d_high_pct,
            "d_low_pct": self.d_low_pct,
            "d_close_pct": self.d_close_pct,
            "vol_vs_prev": self.vol_vs_prev,
            "vol_vs_wave_peak": self.vol_vs_wavepeak,
            "vol_vs_wave_mean": self.vol_vs_wavemean,
            "t_vol_vs_d": self.t_vol_vs_d,
            "mp_D.low_time_i": self.mp_D.low_time_i,
            "mp_D.close_pos": self.mp_D.close_pos,
            "mp_D.amp_pct": self.mp_D.amp_pct,
            "t.open_pct": self.t.open_pct,
            "t.close_pct": self.t.close_pct,
            "t.amp_pct": self.t.amp_pct,
            "t1.open_pct": self.t1.open_pct,
            "pre_close": self.pre_close,
        }

    def field_snapshot(self) -> dict[str, Any]:
        """返回建议记录所需的**依据字段快照**（readme §8：建议可解释）。"""
        return {
            "code": self.code,
            "D": self.D.isoformat(),
            "T": self.T.isoformat(),
            "T1": self.T1.isoformat(),
            "shape_label": self.shape_label,
            "d_amp_pct": self.d_amp_pct,
            "t.open_pct": self.t.open_pct,
            "t.close_pct": self.t.close_pct,
            "t_vol_vs_d": self.t_vol_vs_d,
            "mp_D.low_time_i": self.mp_D.low_time_i,
            "boards": self.boards,
        }

    def factor_context(self) -> FactorContext:
        """构造因子计算上下文（因子只读本对象，保持纯函数）。"""
        return FactorContext(
            code=self.code,
            trade_date=self.D,
            d_bar=self._bar_from(
                self.pre_close,
                self.d_open_pct,
                self.d_high_pct,
                self.d_low_pct,
                self.d_close_pct,
                self.d_vol,
            ),
            d_prev_bar=None,
            t_bar=self._absolute_bar(self.t, self._price(self.pre_close, self.d_close_pct)),
            t1_bar=self._absolute_bar(self.t1, self._price(self.pre_close, self.t.close_pct)),
            minute_d=self._minute_points(self.px_D),
            metrics=self.metrics(),
        )

    # ------------------------------------------------------------ 日线定位

    def buy_day(self, day_field: str) -> date | None:
        """取买入日历日（``day_field`` 为 ``"T"`` / ``"T1"``）。"""
        if day_field == "T":
            return self.T
        if day_field == "T1":
            return self.T1
        return None

    def day_metrics(self, day_field: str) -> DayMetrics:
        """取某日的全天字段对象（``"T"`` / ``"T1"`` / ``"T2"``）。"""
        return {"T": self.t, "T1": self.t1, "T2": self.t2}.get(day_field, DayMetrics())

    def minute_prices(self, day_field: str) -> tuple[float, ...]:
        """取某日的分钟收盘价序列（``"D"`` / ``"T"`` / ``"T1"`` / ``"T2"``）。"""
        return {
            "D": self.px_D,
            "T": self.px_T,
            "T1": self.px_T1,
            "T2": self.px_T2,
        }.get(day_field, ())

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _price(pre_close: float | None, pct: float | None) -> float | None:
        """由昨收与涨幅还原价格。"""
        if pre_close is None or pct is None:
            return None
        return pre_close * (1 + pct)

    @classmethod
    def _bar_from(
        cls,
        pre_close: float | None,
        open_pct: float | None,
        high_pct: float | None,
        low_pct: float | None,
        close_pct: float | None,
        volume: float | None,
    ) -> Bar | None:
        """由昨收 + 各价涨幅构造日线（best-effort，仅作因子回退用）。"""
        open_px = cls._price(pre_close, open_pct)
        high_px = cls._price(pre_close, high_pct)
        low_px = cls._price(pre_close, low_pct)
        close_px = cls._price(pre_close, close_pct)
        if (
            open_px is None
            or high_px is None
            or low_px is None
            or close_px is None
            or pre_close is None
        ):
            return None
        return Bar(
            open=float(open_px),
            high=float(high_px),
            low=float(low_px),
            close=float(close_px),
            pre_close=float(pre_close),
            volume_shares=float(volume or 0.0),
        )

    @classmethod
    def _absolute_bar(cls, day: DayMetrics, pre_close: float | None) -> Bar | None:
        """由全天字段（绝对价）构造日线。"""
        if (
            day.open is None
            or day.high is None
            or day.low is None
            or day.close is None
            or pre_close is None
        ):
            return None
        return Bar(
            open=float(day.open),
            high=float(day.high),
            low=float(day.low),
            close=float(day.close),
            pre_close=float(pre_close),
            volume_shares=float(day.volume or 0.0),
        )

    @staticmethod
    def _minute_points(prices: Sequence[float]) -> tuple[MinutePoint, ...]:
        """由分钟价序列构造 :class:`MinutePoint` 元组（成交量信息缺失，置 0）。"""
        return tuple(
            MinutePoint(
                index=index,
                time_label=minute_time_label(index),
                price=price,
                volume_lots=0.0,
            )
            for index, price in enumerate(prices)
        )


def minute_time_label(index: int) -> str:
    """把分钟序号映射为 ``HH:MM``（readme §1.3：0~119 = 09:31~11:30，120~239 = 13:01~15:00）。"""
    total = 9 * 60 + 31 + index if index < 120 else 13 * 60 + 1 + (index - 120)
    return f"{total // 60:02d}:{total % 60:02d}"


def classify_shape(
    prices: Sequence[float],
    pre_close: float,
    *,
    early_window: int = _EARLY_WINDOW,
    late_index: int = _LATE_INDEX,
    low_pos: float = _LOW_POS,
    dive_drop: float = _DIVE_DROP,
    flat_range: float = _FLAT_RANGE,
) -> str:
    """按分时价格序列判定首阴形态（readme §9 五类）。

    这是**文档化的启发式近似**（生产 analyzer 的标签为准；本函数仅在标签缺失时用于
    从库内分时派生）。判定优先级：

    1. 低点出现在尾盘且尾盘相对盘中明显下挫、收盘贴近低点 → ``尾盘跳水``；
    2. 高点出现在早盘、之后回落且收盘贴近低点 → ``冲高回落``；
    3. 低点出现在早盘且收盘贴近低点 → ``早盘急杀后横盘``；
    4. 全天振幅窄且收盘位置低 → ``低位横盘震荡``；
    5. 其余 → ``单边下跌``。
    """
    if not prices or pre_close <= 0:
        return "单边下跌"
    pct = [price / pre_close - 1 for price in prices]
    count = len(pct)
    low = min(pct)
    high = max(pct)
    low_i = pct.index(low)
    high_i = pct.index(high)
    span = high - low
    close_pos = (pct[-1] - low) / span if span > 0 else 0.5
    ref = pct[min(count - 1, 180)] if count > 180 else pct[count // 2]

    if low_i >= late_index and ref - pct[-1] >= dive_drop and close_pos <= low_pos:
        return "尾盘跳水"
    if high_i < early_window and close_pos <= low_pos:
        return "冲高回落"
    if low_i < early_window and close_pos <= low_pos:
        return "早盘急杀后横盘"
    if close_pos <= low_pos and span <= flat_range:
        return "低位横盘震荡"
    return "单边下跌"
