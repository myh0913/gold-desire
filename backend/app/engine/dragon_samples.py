"""龙回头样本构建（生产数据路径）：从库内日线 + 分时派生结构合格样本。

本模块是**生产数据路径**的样本构建器：给定 :class:`~app.repositories.Repositories` 与
日期区间，扫描日线找「≥2 连板波 + 紧邻首阴」，再叠加分时派生指标，产出
:class:`DragonSample`（字段名严格对齐 readme §10）。

**与 legacy 适配器的分工**：本模块用于真实入库数据的样本生成（后续 backfill 任务驱动）；
:mod:`app.engine.dragon_legacy` 则把已验证的离线样本夹具转成同一 :class:`DragonSample`，
仅用于复现 readme 基线数值。二者产出同一类型，故策略 / 卖出规则 / 组合代码路径完全共用。

样本口径（readme §2）：

1. 至少 **2 连板**（紧邻无间隔，构成「连板波」）；
2. 连板波后紧邻一根**首阴**（``收盘 < 开盘``），该首阴日即基准日 ``D``；
3. 仅取 **60/00 主板非 ST**；
4. ``is_sanbanzu``（三板组）由既有 analyzer 的 ``suspect`` 标识排除——该分析器尚未移植，
   本模块暂置 ``False`` 并在此显式说明（后续接入 analyzer 后回填）。

> 局限：本轮**不做全量一年的回填**（属后续 backfill 任务）。本模块在小型合成数据集上
> 单测其派生字段（连板数、首阴振幅、``t_vol_vs_d``、``low_time_i``、``shape_label``）正确。
> 另假设库内某只股票的日线为**连续交易日**（无缺失交易日），否则「紧邻连板」判定会失真。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.factors.base import Bar, FactorContext, MinutePoint

__all__ = [
    "DayMetrics",
    "DragonSample",
    "MinuteMetrics",
    "build_samples",
    "classify_shape",
    "minute_time_label",
]

#: 主板涨停判定阈值（相对昨收涨幅，含 10% 涨跌幅四舍五入误差）。
_LIMIT_UP_PCT = 0.095

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


# ============================================================ 样本构建


def _is_limit_up(bar: Any) -> bool:
    """是否涨停（主板 10%，含四舍五入容差）。"""
    pre_close = float(bar.pre_close)
    if pre_close <= 0:
        return False
    return float(bar.close) / pre_close - 1 >= _LIMIT_UP_PCT


def _is_one_word(bar: Any) -> bool:
    """是否一字板（涨停且全天未离开涨停价）。"""
    return _is_limit_up(bar) and float(bar.low) >= float(bar.close) - 1e-6


def _day_metrics(bar: Any) -> DayMetrics:
    """由日线构造全天字段对象。"""
    pre_close = float(bar.pre_close)
    return DayMetrics(
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        open_pct=_pct(float(bar.open), pre_close),
        high_pct=_pct(float(bar.high), pre_close),
        low_pct=_pct(float(bar.low), pre_close),
        close_pct=_pct(float(bar.close), pre_close),
        amp_pct=(float(bar.high) - float(bar.low)) / pre_close if pre_close > 0 else None,
        volume=float(bar.volume_shares),
    )


def _pct(price: float, pre_close: float) -> float | None:
    """相对昨收的涨幅（小数口径）。"""
    if pre_close <= 0:
        return None
    return price / pre_close - 1


def _minute_metrics(prices: Sequence[float], pre_close: float | None) -> MinuteMetrics:
    """由分钟价序列计算派生指标。"""
    if not prices or not pre_close or pre_close <= 0:
        return MinuteMetrics(n=len(prices))
    low = min(prices)
    high = max(prices)
    close = prices[-1]
    span = high - low
    return MinuteMetrics(
        n=len(prices),
        high_pct=high / pre_close - 1,
        low_pct=low / pre_close - 1,
        close_pct=close / pre_close - 1,
        amp_pct=span / pre_close,
        low_time_i=prices.index(low),
        close_pos=(close - low) / span if span > 0 else 0.5,
    )


def _mean(values: Sequence[float]) -> float | None:
    """算术平均；空序列返回 ``None``。"""
    return sum(values) / len(values) if values else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """安全比值；分母为 0/缺失时返回 ``None``。"""
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _sample_from_bars(
    code: str,
    name: str,
    bars: Sequence[Any],
    minute_by_date: Mapping[date, tuple[float, ...]],
    index: int,
    wave_start: int,
) -> DragonSample | None:
    """由日线序列与分时映射构造一条样本；分时缺失时返回 ``None``。"""
    first_yin = bars[index]
    wave = bars[wave_start:index]
    d_pre_close = float(first_yin.pre_close)
    px_d = minute_by_date.get(first_yin.trade_date, ())
    if not px_d:
        return None

    wave_volumes = [float(bar.volume_shares) for bar in wave]
    d_vol = float(first_yin.volume_shares)
    peak = max(wave_volumes)
    mean = _mean(wave_volumes)

    def _day_or_none(offset: int) -> Any | None:
        target = index + offset
        return bars[target] if target < len(bars) else None

    t_bar = _day_or_none(1)
    t1_bar = _day_or_none(2)
    t2_bar = _day_or_none(3)
    if t_bar is None or t1_bar is None:
        return None

    return DragonSample(
        code=code,
        name=name,
        D=first_yin.trade_date,
        T=t_bar.trade_date,
        T1=t1_bar.trade_date,
        T2=t2_bar.trade_date if t2_bar is not None else None,
        boards=len(wave),
        wave_vol_trend=_ratio(wave_volumes[-1], wave_volumes[0]),
        wave_one_word_cnt=sum(1 for bar in wave if _is_one_word(bar)),
        wave_peak_vol=peak,
        wave_mean_vol=mean,
        wave_last_vol=wave_volumes[-1],
        wave_first_vol=wave_volumes[0],
        d_amp_pct=(float(first_yin.high) - float(first_yin.low)) / d_pre_close
        if d_pre_close > 0
        else None,
        d_open_pct=_pct(float(first_yin.open), d_pre_close),
        d_high_pct=_pct(float(first_yin.high), d_pre_close),
        d_low_pct=_pct(float(first_yin.low), d_pre_close),
        d_close_pct=_pct(float(first_yin.close), d_pre_close),
        d_vol=d_vol,
        shape_label=classify_shape(px_d, d_pre_close),
        vol_vs_prev=_ratio(d_vol, wave_volumes[-1]),
        vol_vs_wavepeak=_ratio(d_vol, peak),
        vol_vs_wavemean=_ratio(d_vol, mean),
        t_vol_vs_d=_ratio(float(t_bar.volume_shares), d_vol),
        is_sanbanzu=False,
        pre_close=d_pre_close,
        mp_D=_minute_metrics(px_d, d_pre_close),
        t=_day_metrics(t_bar),
        t1=_day_metrics(t1_bar),
        t2=_day_metrics(t2_bar) if t2_bar is not None else DayMetrics(),
        px_D=tuple(px_d),
        px_T=minute_by_date.get(t_bar.trade_date, ()),
        px_T1=minute_by_date.get(t1_bar.trade_date, ()),
        px_T2=minute_by_date.get(t2_bar.trade_date, ()) if t2_bar is not None else (),
    )


async def build_samples(
    repos: Any,
    start: date,
    end: date,
    *,
    min_boards: int = 2,
    lookback_days: int = 120,
    lookahead_days: int = 30,
) -> list[DragonSample]:
    """从库内日线 + 分时构建 ``[start, end]`` 区间内（按基准日 ``D``）的龙回头样本。

    Args:
        repos: :class:`~app.repositories.Repositories` 容器（唯一 DB 读写出口）。
        start: 基准日 ``D`` 的起始日（含）。
        end: 基准日 ``D`` 的结束日（含）。
        min_boards: 连板波最少板数；默认 2（readme §2.1）。
        lookback_days: 向前多取的自然日数（覆盖连板波）。
        lookahead_days: 向后多取的自然日数（覆盖 D+1 / D+2 / D+3）。

    Returns:
        按 ``(D, code)`` 升序的 :class:`DragonSample` 列表。

    Note:
        全量回填（一年）不在本任务范围；本函数用于小规模真实/合成数据的样本生成。
    """
    fetch_start = start - timedelta(days=lookback_days)
    fetch_end = end + timedelta(days=lookahead_days)
    stocks = await repos.stocks.list_all(board="主板")

    samples: list[DragonSample] = []
    for stock in stocks:
        if bool(getattr(stock, "is_st", False)):
            continue
        if not _is_main_board_code(str(stock.code)):
            continue
        bars = await repos.daily_bars.get_range(str(stock.code), fetch_start, fetch_end)
        minute_by_date = await _load_minutes(repos, str(stock.code), bars)
        samples.extend(
            _scan_code(
                str(stock.code), str(stock.name), bars, minute_by_date, start, end, min_boards
            )
        )
    samples.sort(key=lambda sample: (sample.D, sample.code))
    return samples


async def _load_minutes(
    repos: Any, code: str, bars: Sequence[Any]
) -> dict[date, tuple[float, ...]]:
    """批量取该股票各交易日的分钟价序列。"""
    out: dict[date, tuple[float, ...]] = {}
    for bar in bars:
        rows = await repos.minute_bars.get_day(code, bar.trade_date)
        if rows:
            ordered = sorted(rows, key=lambda row: int(row.minute_index))
            out[bar.trade_date] = tuple(float(row.price) for row in ordered)
    return out


def _scan_code(
    code: str,
    name: str,
    bars: Sequence[Any],
    minute_by_date: Mapping[date, tuple[float, ...]],
    start: date,
    end: date,
    min_boards: int,
) -> list[DragonSample]:
    """扫描单只股票的日线，识别「连板波 + 紧邻首阴」并构造样本。"""
    if not bars:
        return []
    limit_flags = [_is_limit_up(bar) for bar in bars]
    out: list[DragonSample] = []
    index = 1
    while index < len(bars):
        bar = bars[index]
        if float(bar.close) >= float(bar.open):  # 非首阴
            index += 1
            continue
        wave_start = index - 1
        while wave_start >= 0 and limit_flags[wave_start]:
            wave_start -= 1
        wave_start += 1
        boards = index - wave_start
        if boards >= min_boards and start <= bar.trade_date <= end:
            sample = _sample_from_bars(code, name, bars, minute_by_date, index, wave_start)
            if sample is not None:
                out.append(sample)
        index += 1
    return out


def _is_main_board_code(code: str) -> bool:
    """是否 60/00 主板（排除创业板 30 / 科创板 68 / 北交所 8、4）。"""
    return code.startswith(("60", "00"))
