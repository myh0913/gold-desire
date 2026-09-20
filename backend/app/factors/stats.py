"""因子有效性统计：按档位 + A/B/C 三段输出样本数、期望、胜率。

**A/B/C 三段约定**（readme §1.5）：A 最老 / B 中段 / C 最新；铁律是「选因子只用 A+B，
C 段只用于验证」，且**任何因子/组合必须三段全部为正才可采纳，禁止只报全量**。
故本模块**始终**输出分段结果，绝不只给聚合值。

**切分口径**：readme §1.5 的切点（2026-01-13 / 2026-05-29）取自当时样本的中位数位置
（§14.7 明确「切点是快照，数据窗口变化后会漂移，复现时需按同口径重新计算」）。
因此本模块按**交易日秩位置**三等分（各占约 1/3 交易日）动态计算切点，从而在原始窗口上
复现文档记录的 383 / 432 / 364 风格切分；也可传入 ``cuts`` 显式固定切点以保证可复现。

**收益口径**：``forward_return`` 为小数（0.0251 = +2.51%），与 readme 表格一致。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.factors.base import BaseFactor, get_factor

__all__ = [
    "SEGMENT_A",
    "SEGMENT_B",
    "SEGMENT_C",
    "SEGMENT_ORDER",
    "SNAPSHOT_CUTS",
    "BucketStats",
    "FactorSample",
    "SegmentStats",
    "effectiveness",
    "segment_cuts",
    "segment_of",
]

SEGMENT_A = "A"
SEGMENT_B = "B"
SEGMENT_C = "C"
SEGMENT_ORDER: tuple[str, str, str] = (SEGMENT_A, SEGMENT_B, SEGMENT_C)

#: readme §1.5 记录的快照切点（A/B 与 B/C 的分界日）；§14.7 说明其随窗口漂移。
SNAPSHOT_CUTS: tuple[date, date] = (date(2026, 1, 13), date(2026, 5, 29))


@dataclass(frozen=True, slots=True)
class FactorSample:
    """单个样本：因子取值 + 该路的前向收益。

    Attributes:
        code: 股票代码。
        trade_date: 样本基准日 D。
        value: 因子取值（数值因子为 ``float``，枚举因子为 ``str``；缺失为 ``None``）。
        forward_return: 前向收益（小数口径，0.0251 = +2.51%）。
    """

    code: str
    trade_date: date
    value: float | str | None
    forward_return: float


@dataclass(frozen=True, slots=True)
class SegmentStats:
    """单段统计。

    Attributes:
        n: 样本数。
        mean_return: 平均前向收益（小数口径）。
        win_rate: 胜率（前向收益 > 0 的占比）。
    """

    n: int
    mean_return: float
    win_rate: float

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {"n": self.n, "mean_return": self.mean_return, "win_rate": self.win_rate}


@dataclass(frozen=True, slots=True)
class BucketStats:
    """单档位统计（含 A/B/C 分段）。

    Attributes:
        bucket: 档位标签。
        n: 该档位样本总数。
        mean_return: 该档位平均前向收益。
        win_rate: 该档位胜率。
        segments: ``{"A": SegmentStats, "B": ..., "C": ...}``，三段互不重叠且 ``n`` 之和等于 ``n``。
    """

    bucket: str
    n: int
    mean_return: float
    win_rate: float
    segments: dict[str, SegmentStats]

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "bucket": self.bucket,
            "n": self.n,
            "mean_return": self.mean_return,
            "win_rate": self.win_rate,
            "segments": {key: value.to_dict() for key, value in self.segments.items()},
        }


def segment_cuts(
    dates: Sequence[date],
    *,
    snapshot: tuple[date, date] | None = None,
) -> tuple[date, date]:
    """按交易日**秩位置**计算 A/B、B/C 切点。

    取排序去重后的交易日列表，按 ``n // 3`` 与 ``2 * n // 3`` 位置切分为三段（各约 1/3）。

    Args:
        dates: 样本交易日集合（顺序不限）。
        snapshot: 交易日不足 3 天时的兜底切点；``None`` 用 :data:`SNAPSHOT_CUTS`。
    """
    ordered = sorted(set(dates))
    if len(ordered) < 3:
        return snapshot if snapshot is not None else SNAPSHOT_CUTS
    count = len(ordered)
    return ordered[count // 3], ordered[2 * count // 3]


def segment_of(trade_date: date, cuts: tuple[date, date]) -> str:
    """判定某交易日属于哪一段（``<cuts[0]`` → A，``<cuts[1]`` → B，否则 C）。"""
    first, second = cuts
    if trade_date < first:
        return SEGMENT_A
    if trade_date < second:
        return SEGMENT_B
    return SEGMENT_C


def _segment_stats(returns: Sequence[float]) -> SegmentStats:
    """由收益序列计算单段统计；空序列返回全零。"""
    if not returns:
        return SegmentStats(n=0, mean_return=0.0, win_rate=0.0)
    total = sum(returns)
    wins = sum(1 for value in returns if value > 0)
    return SegmentStats(
        n=len(returns),
        mean_return=total / len(returns),
        win_rate=wins / len(returns),
    )


def effectiveness(
    factor_id: str,
    samples: Sequence[FactorSample],
    params: Mapping[str, Any] | None = None,
    *,
    cuts: tuple[date, date] | None = None,
) -> list[BucketStats]:
    """按档位输出因子有效性统计（**含 A/B/C 分段**）。

    Args:
        factor_id: 因子标识。
        samples: 样本序列（因子取值 + 前向收益）。
        params: 已解析参数；``None`` 时使用因子代码默认值。档位边界来自参数，
            故调阈值后统计口径同步变化。
        cuts: 显式切点；``None`` 时按样本交易日秩位置动态计算。

    Returns:
        每档位一项 :class:`BucketStats`（按档位声明顺序）；未命中任何档位的样本归入
        ``"其他"`` 档（若存在）。

    Raises:
        FactorRegistryError: 因子未注册。
    """
    factor: type[BaseFactor] = get_factor(factor_id)
    instance = factor()
    effective = instance.default_params()
    if params:
        effective.update(params)

    resolved_cuts = cuts if cuts is not None else segment_cuts([s.trade_date for s in samples])
    buckets = instance.buckets(effective)

    grouped: dict[str, list[FactorSample]] = {bucket.label: [] for bucket in buckets}
    for sample in samples:
        label = instance.classify(sample.value, effective)
        grouped.setdefault(label, []).append(sample)

    report: list[BucketStats] = []
    for label, rows in grouped.items():
        if not rows:
            continue
        segments: dict[str, SegmentStats] = {}
        for name in SEGMENT_ORDER:
            returns = [
                row.forward_return
                for row in rows
                if segment_of(row.trade_date, resolved_cuts) == name
            ]
            segments[name] = _segment_stats(returns)
        aggregate = _segment_stats([row.forward_return for row in rows])
        report.append(
            BucketStats(
                bucket=label,
                n=aggregate.n,
                mean_return=aggregate.mean_return,
                win_rate=aggregate.win_rate,
                segments=segments,
            )
        )
    return report
