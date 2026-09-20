"""已验证样本适配器（**仅用于复现 readme 基线数值**）。

本模块把离线已验证的样本夹具（``tests/fixtures/dragon_samples.json``，取自
``/private/tmp/dr2_samples.json``，1180 条 / 1179 条有效）转换为生产类型
:class:`~app.engine.dragon_samples.DragonSample`，从而让策略 / 卖出规则 / 组合代码路径
**完全共用**于「基线复现」与「真实入库数据」两种场景。

> **重要**：本适配器**不属于生产数据路径**。生产路径是
> :func:`app.engine.dragon_samples.build_samples`（从库内日线 + 分时派生）。
> 本模块存在**唯一目的**是复现 readme §0/§4/§5/§7 记录的一年数据基线数值
> （S2 n=180 / S4 n=165 / 组合触发 311 / 全量 +382.71% 等），供回归测试守护。

夹具为 legacy 结构（字段名与 readme §10 一致但为 JSON 形态，含 ``_px_*`` 分钟价序列与
``mp_D`` / ``t`` / ``t1`` / ``t2`` 嵌套对象）。有效样本口径对齐参考实现：剔除
``_px_D`` 缺失 / ``_px_T`` 缺失 / ``len(_px_D) < 240`` 的记录（原始 1180 → 1179）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from app.engine.dragon_samples import DayMetrics, DragonSample, MinuteMetrics

__all__ = [
    "LEGACY_FIXTURE_PATH",
    "adapt_record",
    "adapt_records",
    "load_fixture",
    "load_fixture_records",
]

#: 夹具路径（``backend/tests/fixtures/dragon_samples.json``）。
LEGACY_FIXTURE_PATH: Path = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "dragon_samples.json"
)

#: 参考实现要求的日线分钟点数（240 点/日）。
_REQUIRED_MINUTE_POINTS = 240


def _as_float(value: Any) -> float | None:
    """转 ``float``；非数值返回 ``None``。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(value: Any) -> int | None:
    """转 ``int``；非数值返回 ``None``。"""
    number = _as_float(value)
    return int(number) if number is not None else None


def _as_prices(value: Any) -> tuple[float, ...]:
    """转分钟价序列（``float`` 元组）。"""
    if not isinstance(value, list):
        return ()
    return tuple(price for price in (_as_float(item) for item in value) if price is not None)


def _day_metrics(raw: Any) -> DayMetrics:
    """把 legacy 全天字段对象转为 :class:`DayMetrics`。"""
    data = raw if isinstance(raw, Mapping) else {}
    return DayMetrics(
        open=_as_float(data.get("open")),
        high=_as_float(data.get("high")),
        low=_as_float(data.get("low")),
        close=_as_float(data.get("close")),
        open_pct=_as_float(data.get("open_pct")),
        high_pct=_as_float(data.get("high_pct")),
        low_pct=_as_float(data.get("low_pct")),
        close_pct=_as_float(data.get("close_pct")),
        amp_pct=_as_float(data.get("amp_pct")),
        volume=_as_float(data.get("volume")),
    )


def _minute_metrics(raw: Any) -> MinuteMetrics:
    """把 legacy ``mp_D`` 对象转为 :class:`MinuteMetrics`。"""
    data = raw if isinstance(raw, Mapping) else {}
    return MinuteMetrics(
        n=_as_int(data.get("n")) or 0,
        high_pct=_as_float(data.get("high_pct")),
        low_pct=_as_float(data.get("low_pct")),
        close_pct=_as_float(data.get("close_pct")),
        amp_pct=_as_float(data.get("amp_pct")),
        low_time_i=_as_int(data.get("low_time_i")),
        close_pos=_as_float(data.get("close_pos")),
    )


def _as_label(value: Any) -> str | None:
    """取字符串标签；非字符串返回 ``None``。"""
    return value if isinstance(value, str) else None


def _as_date(value: Any) -> date | None:
    """把 ``YYYY-MM-DD`` 字符串转 :class:`datetime.date`。"""
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, date):
        return value
    return None


def adapt_record(record: Mapping[str, Any]) -> DragonSample | None:
    """把单条 legacy 记录转为 :class:`DragonSample`；不满足有效样本口径返回 ``None``。"""
    px_d = _as_prices(record.get("_px_D"))
    px_t = _as_prices(record.get("_px_T"))
    if not px_d or not px_t or len(px_d) < _REQUIRED_MINUTE_POINTS:
        return None

    d = _as_date(record.get("D"))
    t = _as_date(record.get("T"))
    t1 = _as_date(record.get("T1"))
    if d is None or t is None or t1 is None:
        return None

    return DragonSample(
        code=str(record.get("code") or ""),
        name=str(record.get("name") or ""),
        D=d,
        T=t,
        T1=t1,
        T2=_as_date(record.get("T2")),
        boards=_as_int(record.get("boards")) or 0,
        wave_vol_trend=_as_float(record.get("wave_vol_trend")),
        wave_one_word_cnt=_as_int(record.get("wave_one_word_cnt")) or 0,
        wave_peak_vol=_as_float(record.get("wave_peak_vol")),
        wave_mean_vol=_as_float(record.get("wave_mean_vol")),
        wave_last_vol=_as_float(record.get("wave_last_vol")),
        wave_first_vol=_as_float(record.get("wave_first_vol")),
        d_amp_pct=_as_float(record.get("d_amp_pct")),
        d_open_pct=_as_float(record.get("d_open_pct")),
        d_high_pct=_as_float(record.get("d_high_pct")),
        d_low_pct=_as_float(record.get("d_low_pct")),
        d_close_pct=_as_float(record.get("d_close_pct")),
        d_vol=_as_float(record.get("d_vol")),
        shape_label=_as_label(record.get("shape_label")),
        vol_vs_prev=_as_float(record.get("vol_vs_prev")),
        vol_vs_wavepeak=_as_float(record.get("vol_vs_wavepeak")),
        vol_vs_wavemean=_as_float(record.get("vol_vs_wavemean")),
        t_vol_vs_d=_as_float(record.get("t_vol_vs_d")),
        is_sanbanzu=bool(record.get("is_sanbanzu", False)),
        pre_close=_as_float(record.get("pre_close")),
        mp_D=_minute_metrics(record.get("mp_D")),
        t=_day_metrics(record.get("t")),
        t1=_day_metrics(record.get("t1")),
        t2=_day_metrics(record.get("t2")),
        px_D=px_d,
        px_T=px_t,
        px_T1=_as_prices(record.get("_px_T1")),
        px_T2=_as_prices(record.get("_px_T2")),
    )


def adapt_records(records: Sequence[Mapping[str, Any]]) -> list[DragonSample]:
    """批量转换，跳过无效记录，并按 ``(D, code)`` 升序返回（与参考实现排序一致）。"""
    samples = [sample for sample in (adapt_record(record) for record in records) if sample]
    samples.sort(key=lambda sample: (sample.D, sample.code))
    return samples


def load_fixture_records(path: Path | str | None = None) -> list[dict[str, Any]]:
    """读取 legacy 夹具原始记录（保留全部 1180 条）。"""
    target = Path(path) if path is not None else LEGACY_FIXTURE_PATH
    with target.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):  # pragma: no cover - 夹具形态固定
        raise ValueError(f"legacy 夹具应为 JSON 数组：{target}")
    return [item for item in data if isinstance(item, dict)]


def load_fixture(path: Path | str | None = None) -> list[DragonSample]:
    """读取夹具并转换为有效 :class:`DragonSample` 列表（默认 1179 条）。"""
    return adapt_records(load_fixture_records(path))
