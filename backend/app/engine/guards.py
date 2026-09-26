"""盘中价 / 竞价撮合价的「除权伪跳变」护栏（整票拒收）。

T+1 链路里，盘中撮合价与库内昨收可能来自**未对齐的复权口径**：除权除息 /
送转日行情未同步调整时，``今日价 / 昨收 - 1`` 会出现远超涨跌幅限制的假跳变
（如 10 转 10 后约 -50%），被单边阈值误判为深低开而放行买入。各策略在生成
建议前调用 :func:`is_suspect_price_move`，命中即**整票拒收**（宁缺勿错，
对齐 :mod:`app.engine.dragon_bars` 日线侧 ``_has_suspect_day`` 的口径）。

已知局限：≤10.5% 的小额除息无法由此拦截（不越涨跌停边界的口径偏移不可辨）。
"""

from __future__ import annotations

# 主板涨跌停 ±10%；放宽 0.5pct 容忍价格精度误差（与日线侧 _SUSPECT_PCT 同源）。
MAX_DAY_MOVE = 0.105


def is_suspect_price_move(
    price: float, pre_close: float, *, max_move: float = MAX_DAY_MOVE
) -> bool:
    """判定 ``price`` 相对 ``pre_close`` 的变动是否越界（可疑 → 整票拒收）。

    正常主板行情 ``|price / pre_close - 1|`` 不可能超过 ±10%，越界只可能是
    除权伪跳变 / 坏数据；任一价格非正（缺失或坏点）同样视为可疑。
    """
    if price <= 0 or pre_close <= 0:
        return True
    return abs(price / pre_close - 1) > max_move
