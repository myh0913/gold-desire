"""龙回头样本构建（生产数据路径）：从库内日线 + 分时派生结构合格样本。

本模块是**生产数据路径**的样本构建器：给定 :class:`~app.repositories.Repositories` 与
日期区间，扫描日线找「≥2 连板波 + 紧邻首阴」，再叠加分时派生指标，产出
:class:`DragonSample`（字段名严格对齐 readme §10）。

**与 legacy 适配器的分工**：本模块用于真实入库数据的样本生成（后续 backfill 任务驱动）；
:mod:`app.engine.dragon_legacy` 则把已验证的离线样本夹具转成同一 :class:`DragonSample`，
仅用于复现 readme 基线数值。二者产出同一类型，故策略 / 卖出规则 / 组合代码路径完全共用。

**模块拆分**（规范：单文件 ≤500 行）：类型与形态分类在 :mod:`app.engine.dragon_model`
（本模块再导出，历史 import 路径不变）；K 线视图 / 涨停等判定助手在
:mod:`app.engine.dragon_bars`；09:25 开盘注入路径在 :mod:`app.engine.dragon_opening`。

样本口径（readme §2）：

1. 至少 **2 连板**（紧邻无间隔，构成「连板波」）；
2. 连板波后紧邻一根**首阴**（``收盘 < 开盘``），该首阴日即基准日 ``D``；
3. 仅取 **60/00 主板非 ST**；
4. ``is_sanbanzu``（三板组）已移植旧 analyzer 判定（恰 3 板 + 后两板一字 +
   量能特征）并**整样本排除**；``suspect``（单日涨跌幅越界 ±10.5%，疑似除权 /
   坏数据）整票拒收（readme §2.3 / §2.5）。

> 局限：本轮**不做全量一年的回填**（属后续 backfill 任务）。本模块在小型合成数据集上
> 单测其派生字段（连板数、首阴振幅、``t_vol_vs_d``、``low_time_i``、``shape_label``）正确。
> 「紧邻连板」按**交易日邻接**判定（``trading_calendar`` 日历缓存可用时按开市日
> 校验，缺采日不再被误当紧邻；日历缺失时回退序列相邻的旧行为）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from app.engine.dragon_bars import (
    _day_metrics,
    _has_suspect_day,
    _is_limit_up,
    _is_main_board_code,
    _is_one_word,
    _is_sanbanzu_wave,
    _mean,
    _minute_metrics,
    _pct,
    _ratio,
    _to_float,
    _with_pre_close,
)
from app.engine.dragon_model import (
    DayMetrics,
    DragonSample,
    MinuteMetrics,
    classify_shape,
    minute_time_label,
)

__all__ = [
    "DayMetrics",
    "DragonSample",
    "MinuteMetrics",
    "build_samples",
    "classify_shape",
    "minute_time_label",
]


def _sample_from_bars(
    code: str,
    name: str,
    bars: Sequence[Any],
    minute_by_date: Mapping[date, tuple[float, ...]],
    index: int,
    wave_start: int,
    pre_wave_bar: Any | None = None,
    *,
    t_metrics: DayMetrics | None = None,
    t1_metrics: DayMetrics | None = None,
    t_date: date | None = None,
    t1_date: date | None = None,
) -> DragonSample | None:
    """由日线序列与分时映射构造一条样本；分时缺失时返回 ``None``。

    ``t_metrics`` / ``t1_metrics`` / ``t_date`` / ``t1_date`` 为**开盘注入**路径
    （:func:`app.engine.dragon_opening.build_opening_samples`）：D+1 / D+2 日线尚未
    入库时，以 09:25 撮合价合成 ``DayMetrics(open=open_pct 相对昨收)``，其余字段留空。
    """
    first_yin = bars[index]
    wave = bars[wave_start:index]
    d_pre_close = _to_float(first_yin.pre_close)
    # 昨收缺失 → 无法计算 D 日涨跌幅与形态，丢弃该样本（不臆断）。
    if d_pre_close is None or d_pre_close <= 0:
        return None
    px_d = minute_by_date.get(first_yin.trade_date, ())
    if not px_d:
        return None

    wave_volumes = [_to_float(bar.volume_shares) or 0.0 for bar in wave]
    d_vol = _to_float(first_yin.volume_shares) or 0.0
    peak = max(wave_volumes)
    mean = _mean(wave_volumes)

    def _day_or_none(offset: int) -> Any | None:
        target = index + offset
        return bars[target] if target < len(bars) else None

    t_bar = _day_or_none(1)
    t1_bar = _day_or_none(2)
    t2_bar = _day_or_none(3)
    # 开盘注入路径：D+1 / D+2 日线未入库时由调用方合成 metrics 与日期。
    if t_bar is None and t_metrics is None:
        return None
    if t1_bar is None and t1_metrics is None:
        return None
    resolved_t_date = t_date or (t_bar.trade_date if t_bar is not None else None)
    resolved_t1_date = t1_date or (t1_bar.trade_date if t1_bar is not None else None)
    if resolved_t_date is None or resolved_t1_date is None:
        return None

    t_final = t_metrics if t_metrics is not None else _day_metrics(t_bar)
    t1_final = t1_metrics if t1_metrics is not None else _day_metrics(t1_bar)
    # t_vol_vs_d 优先取注入/日线口径的成交量；两者都缺（合成 t 无量）时为 None。
    t_volume = (
        t_final.volume
        if t_final.volume is not None
        else (float(t_bar.volume_shares) if t_bar is not None else None)
    )

    return DragonSample(
        code=code,
        name=name,
        D=first_yin.trade_date,
        T=resolved_t_date,
        T1=resolved_t1_date,
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
        t_vol_vs_d=_ratio(t_volume, d_vol),
        is_sanbanzu=_is_sanbanzu_wave(wave, pre_wave_bar),
        pre_close=d_pre_close,
        mp_D=_minute_metrics(px_d, d_pre_close),
        t=t_final,
        t1=t1_final,
        t2=_day_metrics(t2_bar) if t2_bar is not None else DayMetrics(),
        px_D=tuple(px_d),
        px_T=minute_by_date.get(resolved_t_date, ()),
        px_T1=minute_by_date.get(resolved_t1_date, ()),
        px_T2=minute_by_date.get(t2_bar.trade_date, ()) if t2_bar is not None else (),
    )


#: 交易日历在 ``pool_snapshot`` 中的池名（与 ``app.ingest.tasks.CALENDAR_POOL_NAME``
#: 同值；此处就地声明，避免 engine → ingest 跨域依赖）。
_CALENDAR_POOL_NAME = "trading_calendar"


async def _load_trading_dates(
    repos: Any, fetch_start: date, fetch_end: date, *, anchor: date | None = None
) -> set[date] | None:
    """取日历缓存内的开市日期集合；无缓存 / 载荷异常返回 ``None``（回退旧行为）。

    日历行按「写入时的目标交易日」为键（生产由调度器每日刷新，键=当日），
    故除区间两端外再尝试 ``anchor``（调用方的判定基准日）以提高命中率。
    """
    try:
        row = await repos.pool_snapshot.get(fetch_end, _CALENDAR_POOL_NAME)
        if row is None:
            row = await repos.pool_snapshot.get(fetch_start, _CALENDAR_POOL_NAME)
        if row is None and anchor is not None:
            row = await repos.pool_snapshot.get(anchor, _CALENDAR_POOL_NAME)
    except Exception:  # pragma: no cover - 仓储异常按无日历处理
        return None
    if row is None:
        return None
    raw = row.payload.get("dates") or []
    try:
        dates = {date.fromisoformat(str(item)) for item in raw}
    except ValueError:
        return None
    return dates or None


async def build_samples(
    repos: Any,
    start: date,
    end: date,
    *,
    min_boards: int = 2,
    lookback_days: int = 120,
    lookahead_days: int = 30,
    include_pending: bool = False,
) -> list[DragonSample]:
    """从库内日线 + 分时构建 ``[start, end]`` 区间内（按基准日 ``D``）的龙回头样本。

    Args:
        repos: :class:`~app.repositories.Repositories` 容器（唯一 DB 读写出口）。
        start: 基准日 ``D`` 的起始日（含）。
        end: 基准日 ``D`` 的结束日（含）。
        min_boards: 连板波最少板数；默认 2（readme §2.1）。
        lookback_days: 向前多取的自然日数（覆盖连板波）。
        lookahead_days: 向后多取的自然日数（覆盖 D+1 / D+2 / D+3）。
        include_pending: **轻量建池**口径——D+1/D+2 日线未入库（如收盘后即建池，
            ``D = 当日``）时仍产出样本，T / T1 以空指标 + 日历推算日期占位；
            默认 ``False``（缺后继日线即丢弃样本）。

    Returns:
        按 ``(D, code)`` 升序的 :class:`DragonSample` 列表（三板组 / suspect 票已排除）。

    Note:
        全量回填（一年）不在本任务范围；本函数用于小规模真实/合成数据的样本生成。
        「紧邻连板」按**交易日邻接**判定（日历缓存 ``trading_calendar`` 可用时），
        缺失时回退序列相邻的旧行为。
    """
    fetch_start = start - timedelta(days=lookback_days)
    fetch_end = end + timedelta(days=lookahead_days)
    trading_dates = await _load_trading_dates(repos, fetch_start, fetch_end, anchor=end)
    stocks = await repos.stocks.list_all(board="主板")

    samples: list[DragonSample] = []
    for stock in stocks:
        if bool(getattr(stock, "is_st", False)):
            continue
        if not _is_main_board_code(str(stock.code)):
            continue
        bars = await repos.daily_bars.get_range(str(stock.code), fetch_start, fetch_end)
        if not bars:
            continue
        # 回填昨收（库中 pre_close 恒为 NULL），必须在 suspect 检测**之前**——
        # 否则除权检测拿不到昨收，等于死代码。
        bars = _with_pre_close(bars)
        if _has_suspect_day(bars):
            # 疑似除权或坏数据（单日涨跌幅越界 ±10.5%）→ 整票拒收。
            continue
        minute_by_date = await _load_minutes(repos, str(stock.code), bars)
        samples.extend(
            _scan_code(
                str(stock.code),
                str(stock.name),
                bars,
                minute_by_date,
                start,
                end,
                min_boards,
                trading_dates,
                pending_ok=include_pending,
            )
        )
    samples.sort(key=lambda sample: (sample.D, sample.code))
    return samples


async def _load_minutes(
    repos: Any, code: str, bars: Sequence[Any]
) -> dict[date, tuple[float, ...]]:
    """批量取该股票各交易日的分钟价序列（单次范围查询，避免逐日 N 次查询）。"""
    if not bars:
        return {}
    start = min(bar.trade_date for bar in bars)
    end = max(bar.trade_date for bar in bars)
    rows = await repos.minute_bars.get_range(code, start, end)
    grouped: dict[date, list[float]] = {}
    for row in rows:
        grouped.setdefault(row.trade_date, []).append(float(row.price))
    return {day: tuple(prices) for day, prices in grouped.items()}


def _next_trading_date(after: date, trading_dates: set[date] | None) -> date:
    """推算 ``after`` 之后的下一交易日；日历缺失时按自然日 +1 占位。

    仅用于轻量建池的 T / T1 **日期占位**（卖出撮合不依赖该值）。
    """
    if trading_dates:
        later = sorted(day for day in trading_dates if day > after)
        if later:
            return later[0]
    return after + timedelta(days=1)


def _dates_adjacent(prev_d: date, cur_d: date, trading_dates: set[date] | None) -> bool:
    """两根日线是否为**相邻交易日**（中间无缺失的开市日）。

    日线序列只含已入库数据，缺采的交易日会造成「序列相邻、交易日不相邻」——
    直接把隔了假期的两根 K 线当紧邻连板会失真（模块 docstring 历史局限）。
    ``trading_dates`` 缺失（日历未入库）时回退旧行为按序列相邻处理。
    """
    if trading_dates is None:
        return True
    return not any(prev_d < day < cur_d for day in trading_dates)


def _scan_code(
    code: str,
    name: str,
    bars: Sequence[Any],
    minute_by_date: Mapping[date, tuple[float, ...]],
    start: date,
    end: date,
    min_boards: int,
    trading_dates: set[date] | None = None,
    *,
    pending_ok: bool = False,
) -> list[DragonSample]:
    """扫描单只股票的日线，识别「连板波 + 紧邻首阴」并构造样本。

    ``pending_ok=True``（轻量建池）时，D+1 / D+2 日线未入库的样本仍产出：
    缺失日以空 :class:`DayMetrics` + 日历推算日期占位（经既有开盘注入参数传入，
    不改变 :func:`_sample_from_bars` 的严格口径）。
    """
    if not bars:
        return []
    limit_flags = [_is_limit_up(bar) for bar in bars]
    # consecutive[i]：bars[i-1] 与 bars[i] 是否为相邻交易日（无缺失开市日）。
    consecutive = [False] * len(bars)
    for i in range(1, len(bars)):
        consecutive[i] = _dates_adjacent(bars[i - 1].trade_date, bars[i].trade_date, trading_dates)
    out: list[DragonSample] = []
    index = 1
    while index < len(bars):
        bar = bars[index]
        # 非首阴，或首阴与波末板之间隔了缺失交易日 → 不构成「紧邻首阴」。
        if float(bar.close) >= float(bar.open) or not consecutive[index]:
            index += 1
            continue
        wave_start = index - 1
        while (
            wave_start >= 0
            and limit_flags[wave_start]
            and (wave_start == index - 1 or consecutive[wave_start + 1])
        ):
            wave_start -= 1
        wave_start += 1
        boards = index - wave_start
        if boards >= min_boards and start <= bar.trade_date <= end:
            # 波前一日仅当与波首板相邻时才参与三板组判定（否则视为缺失）。
            pre_wave_bar = (
                bars[wave_start - 1] if wave_start > 0 and consecutive[wave_start] else None
            )
            t_metrics = t1_metrics = None
            t_date = t1_date = None
            if pending_ok:
                # 轻量建池：后继日线未入库时以空指标占位（建池只需 D 日结构）。
                t_bar = bars[index + 1] if index + 1 < len(bars) else None
                t1_bar = bars[index + 2] if index + 2 < len(bars) else None
                if t_bar is None:
                    t_date = _next_trading_date(bar.trade_date, trading_dates)
                    t_metrics = DayMetrics()
                if t1_bar is None:
                    t1_date = _next_trading_date(t_date or bar.trade_date, trading_dates)
                    t1_metrics = DayMetrics()
            sample = _sample_from_bars(
                code,
                name,
                bars,
                minute_by_date,
                index,
                wave_start,
                pre_wave_bar,
                t_metrics=t_metrics,
                t1_metrics=t1_metrics,
                t_date=t_date,
                t1_date=t1_date,
            )
            if sample is not None and not sample.is_sanbanzu:
                out.append(sample)
        index += 1
    return out
