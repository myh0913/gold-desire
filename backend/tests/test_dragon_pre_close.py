"""dragon 样本昨收回填回归测试（`pre_close` 恒为 NULL 导致的必崩缺陷）。

**背景**：上游日线不带昨收，库中 ``daily_bars.pre_close`` 实测 4593/4593 全为 NULL。
``_is_limit_up`` 原先直接 ``float(bar.pre_close)`` → ``TypeError``，导致 dragon 策略
每次运行必崩（当日 239 次 pool + 239 次 intraday 全落 error 报告，建议零产出）。

**锁住的行为**：

1. ``_with_pre_close`` 用**序列中前一根日线的收盘价**回填昨收，且返回只读副本
   （不改动入参，避免把 ORM 实例写脏回库）；
2. 昨收缺失时 ``_is_limit_up`` **判否而不抛异常**；``_day_metrics`` 留空涨跌幅字段；
3. 回填后涨停判定口径正确（+10% 命中、+9.9% 不命中）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from app.engine.dragon_bars import _BarView, _day_metrics, _is_limit_up, _with_pre_close

DAY0 = date(2026, 9, 14)


@dataclass(frozen=True)
class _FakeBar:
    """模拟 ORM 日线行：``pre_close`` 恒为 ``None``（与生产库一致）。"""

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume_shares: int
    pre_close: float | None = None


def _seq(closes: list[float]) -> list[_FakeBar]:
    return [
        _FakeBar(
            trade_date=DAY0 + timedelta(days=index),
            open=close,
            high=close,
            low=close,
            close=close,
            volume_shares=1_000,
        )
        for index, close in enumerate(closes)
    ]


def test_with_pre_close_backfills_from_previous_close() -> None:
    original = _seq([10.0, 11.0, 12.1])
    views = _with_pre_close(original)

    assert [view.pre_close for view in views] == [None, 10.0, 11.0]
    # 只读副本：原对象未被改动（否则 ORM 会被写脏回库）。
    assert all(bar.pre_close is None for bar in original)
    assert isinstance(views[0], _BarView)


def test_existing_pre_close_wins() -> None:
    """行上已有昨收时优先用它，不用序列前值。"""
    bars = list(_seq([10.0, 11.0]))
    bars[1] = _FakeBar(
        trade_date=bars[1].trade_date,
        open=11.0,
        high=11.0,
        low=11.0,
        close=11.0,
        volume_shares=1_000,
        pre_close=9.5,
    )

    assert _with_pre_close(bars)[1].pre_close == 9.5


def test_is_limit_up_returns_false_without_pre_close() -> None:
    """回归：昨收缺失时必须判否，而不是抛 TypeError。"""
    bar = _FakeBar(
        trade_date=DAY0, open=11.0, high=11.0, low=11.0, close=11.0, volume_shares=1_000
    )
    assert _is_limit_up(bar) is False


def test_day_metrics_does_not_crash_without_pre_close() -> None:
    """昨收缺失 → 涨跌幅/振幅留空，成交量仍取到。"""
    bar = _FakeBar(
        trade_date=DAY0, open=11.0, high=11.5, low=10.5, close=11.0, volume_shares=2_000
    )
    metrics = _day_metrics(bar)

    assert metrics.close == pytest.approx(11.0)
    assert metrics.volume == pytest.approx(2_000.0)
    assert metrics.close_pct is None
    assert metrics.amp_pct is None


def test_limit_up_detection_after_backfill() -> None:
    """回填后涨停口径正确：+10% 命中；低于容差（0.095）不命中。"""
    views = _with_pre_close(_seq([10.0, 11.0]))  # 第二根 +10%
    assert _is_limit_up(views[1]) is True

    views = _with_pre_close(_seq([10.0, 10.9]))  # +9.0% < 容差 9.5%
    assert _is_limit_up(views[1]) is False


def test_first_bar_has_no_pre_close() -> None:
    """序列首根无昨收 → 判否（不臆断）。"""
    views = _with_pre_close(_seq([10.0]))
    assert views[0].pre_close is None
    assert _is_limit_up(views[0]) is False
