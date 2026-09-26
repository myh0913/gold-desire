"""eltdx provider 单元测试：分钟成交额合并口径 / 代码映射 / 竞价撮合形态。

用 fake 客户端（SimpleNamespace）替代 TDX TCP，覆盖纯逻辑分支，无网络依赖。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from app.datasources.providers.eltdx import EltdxProvider, _today_sh, to_eltdx_code


def _minute(label: str, price: float, volume: float) -> SimpleNamespace:
    """分时点对象形态（time_label / price 元 / volume 手）。"""
    return SimpleNamespace(time_label=label, price=price, volume=volume)


def _bar(offset: int, amount: float) -> SimpleNamespace:
    """1m K 线对象形态（time 为 datetime / amount 元）。"""
    return SimpleNamespace(
        time=datetime(2026, 6, 2, 9, 31) + timedelta(minutes=offset),
        amount=amount,
    )


def _provider_with(client: Any) -> EltdxProvider:
    """绕过 SDK 惰性创建，直接注入 fake 客户端。"""
    provider = EltdxProvider()
    provider._client = client
    return provider


# ============================================================ 1. 代码映射


def test_to_eltdx_code() -> None:
    """沪深后缀正常映射；北交所等未知后缀显式报错（不静默映射为 sz）。"""
    assert to_eltdx_code("600519.SH") == "sh600519"
    assert to_eltdx_code("000001.SZ") == "sz000001"
    with pytest.raises(ValueError, match="仅支持"):
        to_eltdx_code("430047.BJ")


# ============================================================ 2. 分钟成交额


def test_minute_bars_today_merges_amount() -> None:
    """当日请求：分时点按 time_label 对齐附加 1m K 线成交额（元）。"""
    series = SimpleNamespace(
        points=[_minute("09:31", 10.0, 120.0), _minute("09:32", 10.1, 80.0)]
    )
    bars = SimpleNamespace(bars=[_bar(0, 100.5), _bar(1, 200.25)])
    client = SimpleNamespace(
        minutes=SimpleNamespace(today=lambda code: series),
        bars=SimpleNamespace(get=lambda code, period, count: bars),
    )
    payload = _provider_with(client)._fetch_sync(
        "minute_bars", "600519.SH", _today_sh()
    )
    assert [p["amount"] for p in payload["points"]] == [100.5, 200.25]


def test_minute_bars_history_skips_amount() -> None:
    """历史请求：SDK 无法取历史 1m K 线 → 不拉 K 线、amount 留空（宁缺勿错）。"""
    series = SimpleNamespace(points=[_minute("09:31", 10.0, 120.0)])
    calls: list[str] = []

    def _unexpected_bars(code: str, period: str, count: int) -> Any:
        calls.append("bars")
        return SimpleNamespace(bars=[])

    client = SimpleNamespace(
        minutes=SimpleNamespace(history=lambda code, date: series),
        bars=SimpleNamespace(get=_unexpected_bars),
    )
    payload = _provider_with(client)._fetch_sync(
        "minute_bars", "600519.SH", "2026-06-02"
    )
    assert payload["points"][0]["amount"] is None
    assert calls == [], "历史请求不应触发 1m K 线拉取"


# ============================================================ 3. 竞价撮合形态


def test_opening_match_payload_shape() -> None:
    """opening_match payload 为 ``{"matches": [...]}`` 列表形态（映射层契约）。"""
    om = SimpleNamespace(price=10.5, volume=300.0, time_label="09:25")
    client = SimpleNamespace(
        trades=SimpleNamespace(opening_match_history=lambda code, date: om),
    )
    payload = _provider_with(client)._fetch_sync(
        "opening_match", "600519.SH", "2026-06-02"
    )
    assert payload == {
        "matches": [{"price": 10.5, "volume": 300.0, "time_label": "09:25"}]
    }
