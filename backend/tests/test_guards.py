"""guards：除权伪跳变护栏（is_suspect_price_move）纯函数测试。"""

from __future__ import annotations

import pytest
from app.engine.guards import MAX_DAY_MOVE, is_suspect_price_move


@pytest.mark.parametrize(
    ("price", "pre_close", "expected"),
    [
        (10.0, 10.0, False),  # 平盘
        (11.0, 10.0, False),  # +10% 涨停边界内
        (9.0, 10.0, False),  # -10% 跌停边界内
        (10.5, 10.0, False),  # +5%
        (11.06, 10.0, True),  # +10.6% 越界（涨跌停不可能出现）
        (8.9, 10.0, True),  # -11% 越界（除权伪跳变量级）
        (5.0, 10.0, True),  # -50%：10 转 10 未复权的典型场景
        (10.0, 0.0, True),  # 昨收非正 → 可疑
        (0.0, 10.0, True),  # 价格非正 → 可疑
    ],
)
def test_is_suspect_price_move(price: float, pre_close: float, expected: bool) -> None:
    """±10% 内正常，越界 / 非正值可疑。"""
    assert is_suspect_price_move(price, pre_close) is expected


def test_max_day_move_aligned_with_dragon_samples() -> None:
    """阈值与 dragon_bars 日线侧 _SUSPECT_PCT 同源（防漂移）。"""
    from app.engine import dragon_bars

    assert MAX_DAY_MOVE == dragon_bars._SUSPECT_PCT
