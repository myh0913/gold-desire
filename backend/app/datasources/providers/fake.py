"""内存假数据源：覆盖 Phase-1 全部能力，供框架与映射测试使用（零网络）。

payload **刻意采用与契约不同的字段名与单位**：

- 代码用 ``symbol``（``.SS`` 后缀）而非 ``code``；
- 连板数用 ``limit_up_days``、涨停时间用毫秒时间戳；
- 成交量用 ``vol``（手）、成交额用 ``amount``（万元）、市值用 ``mkt_cap``（亿元）；
- 换手/涨跌幅用百分数（``12.5`` 而非 ``0.125``）。

映射层（``mappings/defs/fake.py``）负责把上述差异收敛到契约。
"""

from __future__ import annotations

import copy
from typing import Any, ClassVar

from app.core.errors import UpstreamError
from app.datasources.base import BaseProvider, SourceKind
from app.datasources.registry import register_provider

__all__ = ["FakeProvider", "default_payloads"]


def default_payloads() -> dict[str, Any]:
    """返回 fake 源各能力的默认（上游口径）payload。"""
    return {
        "daily_bars": {
            "code": 0,
            "result": {
                "rows": [
                    {
                        "symbol": "600519.SS",
                        "date_ms": 1789660800000,
                        "o": 1500.0,
                        "h": 1520.0,
                        "l": 1490.0,
                        "c": 1510.0,
                        "pc": 1495.0,
                        "vol": 1234.5,
                        "amount": 3456.78,
                    }
                ]
            },
        },
        "limit_up_pool": {
            "data": {
                "items": [
                    {
                        "symbol": "300750.SZ",
                        "stock_chi_name": "宁德时代",
                        "limit_up_days": 3,
                        "first_limit_up_time_ms": 1789694700000,
                        "seal_money": 1234.5,
                        "open_cnt": 2,
                        "turnover": 12.5,
                        "amount": 45678.9,
                        "mkt_cap": 1.2,
                        "pool": "limit_up",
                    }
                ]
            }
        },
        "ladder": {
            "matrix": [
                {
                    "trade_day": "20260918",
                    "symbol": "600519.SS",
                    "name": "贵州茅台",
                    "board_cnt": 2,
                    "first_seal_ms": 1789694700000,
                }
            ]
        },
        "trading_calendar": {
            "days": [
                {"cal_date": "20260918", "open_flag": 1},
                {"cal_date": "20260919", "open_flag": 0},
            ]
        },
        "market_sentiment": {
            "data": {
                "trade_day": "2026-09-18",
                "temperature": 62.5,
                "stage": "发酵",
                "up_limit": 45,
                "down_limit": 3,
                "broken": 8,
                "broken_pct": 15.1,
                "rise": 2800,
                "fall": 1900,
                "max_board": 5,
                "premium": 2.3,
            }
        },
        "theme_rank": {
            "plates": [
                {
                    "trade_day": "2026-09-18",
                    "rank_no": 1,
                    "plate_name": "人工智能",
                    "core_avg": 5.2,
                    "desc": "算力与应用共振",
                    "core_cnt": 12,
                }
            ]
        },
        "theme_stocks": {
            "stocks": [
                {
                    "trade_day": "2026-09-18",
                    "plate_name": "人工智能",
                    "symbol": "002230.SZ",
                    "stock_name": "科大讯飞",
                    "last": 45.6,
                    "pct_chg": 9.98,
                    "turnover": 8.5,
                    "days": 2,
                }
            ]
        },
        "newsflash": {
            "messages": [
                {
                    "created_at_ms": 1789784100000,
                    "title": "算力需求超预期",
                    "digest": "多家公司上调全年指引",
                    "stock_symbols": ["600519.SS", "000001.SZ"],
                    "tags": ["人工智能", "算力"],
                }
            ]
        },
        "minute_bars": {
            "points": [
                {"time_label": "09:31", "price": 39.16, "volume": 1673.0},
                {"time_label": "09:32", "price": 39.03, "volume": 1970.0},
                {"time_label": "09:33", "price": 39.2, "volume": 1210.0},
            ]
        },
        "opening_match": {
            "matches": [{"price": 39.1, "volume": 5200.0, "time_label": "09:25"}]
        },
    }


@register_provider
class FakeProvider(BaseProvider):
    """内存假数据源：``fetch`` 返回深度拷贝的固定 payload，可注入自定义数据。"""

    source_id: ClassVar[str] = "fake"
    label: ClassVar[str] = "内存假数据源（测试用）"
    kind: ClassVar[SourceKind] = SourceKind.FAKE
    capabilities: ClassVar[tuple[str, ...]] = (
        "daily_bars",
        "limit_up_pool",
        "ladder",
        "trading_calendar",
        "market_sentiment",
        "theme_rank",
        "theme_stocks",
        "newsflash",
        "minute_bars",
        "opening_match",
    )
    rate_limit_per_min: ClassVar[int] = 600

    def __init__(self, payloads: dict[str, Any] | None = None) -> None:
        self._payloads = payloads if payloads is not None else default_payloads()

    def set_payload(self, capability: str, payload: Any) -> None:
        """覆盖某能力的 payload（测试构造缺字段/非法值场景用）。"""
        self._payloads[capability] = payload

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        """返回该能力的固定 payload 深拷贝；未覆盖的能力报错。"""
        if capability not in self._payloads:
            raise UpstreamError(
                f"fake 源不支持能力 {capability!r}",
                detail={"source": self.source_id, "capability": capability},
            )
        return copy.deepcopy(self._payloads[capability])
