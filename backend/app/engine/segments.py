"""A/B/C 三段互不重叠切分（readme §1.5 / §14.7）。

方法论铁律（readme §1.5）：

- **A 最老** / **B 中段** / **C 最新**，三段互不重叠；
- **选因子只用 A+B；C 段只用于验证，绝不参与选择**；任何因子/组合必须三段全部为正才可
  采纳，**禁止只报全量结果**。

切分口径（复现参考实现 ``dr3_step.py``）：

- ``cut_bc`` = 快照切点 ``2026-05-29``（readme §1.5 记录的 B/C 分界日）；
- ``cut_ab`` = 对 ``D < cut_bc`` 的样本按 **D 排序后的中位记录** 的 ``D``（秩位置中位数），
  在原始窗口上复现出 ``2026-01-13``，进而得到 383 / 432 / 364 的三段样本数。

> **切点是快照**（readme §14.7）：切点取自当时样本的中位数位置，数据窗口变化后会漂移，
> 复现时须按同口径重新计算；故 :func:`split_segments` 也支持显式传入 ``cuts`` 以固定切点。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.engine.dragon_samples import DragonSample

__all__ = [
    "SEGMENT_A",
    "SEGMENT_ALL",
    "SEGMENT_B",
    "SEGMENT_C",
    "SEGMENT_ORDER",
    "SNAPSHOT_CUTS",
    "Segments",
    "segment_of",
    "split_segments",
]

SEGMENT_A = "A"
SEGMENT_B = "B"
SEGMENT_C = "C"
#: 全量段（非互斥三段之一，仅用于汇总展示）。
SEGMENT_ALL = "全量"

#: 展示顺序：先三段，再全量。
SEGMENT_ORDER: tuple[str, str, str, str] = (SEGMENT_A, SEGMENT_B, SEGMENT_C, SEGMENT_ALL)

#: readme §1.5 记录的快照切点（A/B 与 B/C 的分界日）；§14.7 说明其随窗口漂移。
SNAPSHOT_CUTS: tuple[date, date] = (date(2026, 1, 13), date(2026, 5, 29))


def _median_cut(dates: Sequence[date], upper: date) -> date:
    """取 ``D < upper`` 的样本按 D 排序后的中位记录日（秩位置中位数）。"""
    ordered = sorted(day for day in dates if day < upper)
    if not ordered:
        return upper
    return ordered[len(ordered) // 2]


def split_segments(
    samples: Sequence[DragonSample],
    *,
    cuts: tuple[date, date] | None = None,
) -> Segments:
    """把样本切成互不重叠的 A/B/C 三段。

    Args:
        samples: 样本序列（顺序不限）。
        cuts: 显式切点 ``(cut_ab, cut_bc)``；``None`` 时按 readme §1.5 口径动态计算
            （``cut_bc`` 用 :data:`SNAPSHOT_CUTS` 的快照值，``cut_ab`` 用中位记录日）。

    Returns:
        :class:`Segments`。
    """
    ordered = sorted(samples, key=lambda sample: (sample.D, sample.code))
    if cuts is not None:
        cut_ab, cut_bc = cuts
    else:
        cut_bc = SNAPSHOT_CUTS[1]
        cut_ab = _median_cut([sample.D for sample in ordered], cut_bc)
    return Segments(
        cuts=(cut_ab, cut_bc),
        buckets={
            SEGMENT_A: tuple(s for s in ordered if cut_ab > s.D),
            SEGMENT_B: tuple(s for s in ordered if cut_ab <= s.D < cut_bc),
            SEGMENT_C: tuple(s for s in ordered if cut_bc <= s.D),
            SEGMENT_ALL: tuple(ordered),
        },
    )


def segment_of(trade_date: date, cuts: tuple[date, date]) -> str:
    """判定某基准日属于哪一段（``<cuts[0]`` → A，``<cuts[1]`` → B，否则 C）。"""
    first, second = cuts
    if trade_date < first:
        return SEGMENT_A
    if trade_date < second:
        return SEGMENT_B
    return SEGMENT_C


@dataclass(frozen=True, slots=True)
class Segments:
    """三段切分结果与按段聚合的辅助入口。

    Attributes:
        cuts: ``(cut_ab, cut_bc)`` 切点。
        buckets: ``{"A": (...), "B": (...), "C": (...), "全量": (...)}``。
    """

    cuts: tuple[date, date]
    buckets: dict[str, tuple[DragonSample, ...]]

    def get(self, name: str) -> tuple[DragonSample, ...]:
        """取某段样本（``"A"`` / ``"B"`` / ``"C"`` / ``"全量"``）。"""
        return self.buckets.get(name, ())

    def names(self) -> tuple[str, ...]:
        """按展示顺序返回段名（A/B/C/全量）。"""
        return SEGMENT_ORDER

    def items(self) -> Iterator[tuple[str, tuple[DragonSample, ...]]]:
        """按展示顺序迭代 ``(段名, 样本)``。"""
        for name in SEGMENT_ORDER:
            yield name, self.get(name)

    def counts(self) -> dict[str, int]:
        """各段样本数。"""
        return {name: len(self.get(name)) for name in SEGMENT_ORDER}

    def dates(self, name: str = SEGMENT_ALL) -> tuple[date, ...]:
        """某段涉及的交易日集合（升序去重）。"""
        return tuple(sorted({sample.D for sample in self.get(name)}))
