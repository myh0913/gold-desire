"""同花顺（hithink）各能力的声明式字段映射。

源字段口径（对齐 ``/api/a-share/*`` 端点实测响应，样例见 ``/tmp/ds_probe_out.json``）：

- ``thscode`` 形如 ``600519.SH`` / ``000001.SZ``，交给 ``normalize_code`` 归一为项目标准码；
- 日线 ``/prices/historical``：``data.thscode`` 在 **data 层**（祖先作用域），
  ``data.item[]`` 每根 K 线含 ``date_ms``（毫秒时间戳）/ ``*_price`` / ``volume`` / ``turnover``；
  **该端点不返回昨收**，故 ``pre_close`` 留空（由上层按前一根 K 线补齐）；
- 涨停池 ``/special-data/limit-up-pool``：``data.item[]`` 含 ``limit_up_time``（已是 ``HH:MM``）、
  ``continue_day_cnt``、``seal_money``；**该端点不返回换手率/成交额/总市值**，对应字段留空；
- 连板天梯 ``/special-data/limit-up-ladder``：``data.item[]`` 每项为 **一天**
  （``date`` + ``boards`` 六个分组 ``two_board`` … ``seven_over``），
  故 ``record_path`` 直接摊平到「一票一行」，日期由 ``^date`` 从上级 item 回归；
- 交易日历 ``/calendar/trading-days``：``data.item[]`` 仅含交易日，语义上 ``is_open`` 恒为 True。

单位口径（**该源大量字段已是元/股，不做放大**）：

- ``volume`` 已是**股**（非手）→ 直接 ``to_int`` 写入 ``volume_shares``；
- ``turnover`` / ``seal_money`` 已是**元**（非万元）→ 直接 ``to_float``。

缺失语义：契约必需字段用 ``REQUIRED``（缺失即拒绝该记录，绝不填 0/空串）；
上游不提供或本身可空的字段用 ``required=False`` → ``None``（「能算的算、算不出的留空」）。
"""

from __future__ import annotations

from app.datasources.mappings.dsl import CapabilityMapping, FieldMap
from app.datasources.mappings.registry import register_mapping

__all__ = ["HITHINK_MAPPINGS", "register_hithink_mappings"]

_DAILY_BAR = CapabilityMapping(
    source_id="hithink",
    capability="daily_bars",
    record_path="data.item",
    notes="日线：data.thscode（祖先）+ data.item[] 每根 K 线；"
    "date_ms(ms) / *_price / volume(股) / turnover(元)；源无昨收",
    fields=(
        FieldMap("code", "^thscode", "normalize_code"),
        FieldMap("trade_date", "date_ms", "ms_to_date"),
        FieldMap("open", "open_price", "to_float"),
        FieldMap("high", "high_price", "to_float"),
        FieldMap("low", "low_price", "to_float"),
        FieldMap("close", "close_price", "to_float"),
        FieldMap("pre_close", "pre_close_price", "to_float", required=False),
        FieldMap("volume_shares", "volume", "to_int"),
        FieldMap("amount_yuan", "turnover", "to_float"),
    ),
)

_LIMIT_UP_STOCK = CapabilityMapping(
    source_id="hithink",
    capability="limit_up_pool",
    record_path="data.item",
    notes="涨停池：thscode / name / continue_day_cnt / limit_up_time(HH:MM) / "
    "seal_money(元) / max_seal_money(元)；换手率、总市值该端点不提供（留空）；"
    "本源作为 xuangutong 的补数源（封单金额/最大封单金额/首封时间）",
    static={"pool_type": "limit_up"},
    fields=(
        FieldMap("code", "thscode", "normalize_code"),
        FieldMap("name", "name", "to_str"),
        FieldMap("continue_days", "continue_day_cnt", "to_int"),
        FieldMap("limit_up_time", "limit_up_time", "hhmm_from_str", required=False),
        FieldMap("seal_amount_yuan", "seal_money", "to_float"),
        FieldMap("max_seal_amount_yuan", "max_seal_money", "to_float", required=False),
        FieldMap("open_times", "break_limit_up_times", "to_int", required=False),
        FieldMap("turnover_rate", "turnover_ratio", "ratio_passthrough", required=False),
        FieldMap("amount_yuan", "amount", "to_float", required=False),
        FieldMap("market_cap_yuan", "market_cap", "to_float", required=False),
    ),
)

#: 涨停池补数轮（第 8 轮）：与 ``limit_up_pool`` 同端点同形状，主备链固定 hithink；
#: 写入器只合并回填封单金额/最大封单金额/首封时间，不整行替换主源数据。
_LIMIT_UP_SUPPLEMENT = CapabilityMapping(
    source_id="hithink",
    capability="limit_up_pool_supplement",
    record_path="data.item",
    notes="涨停池补数：seal_money(元) / max_seal_money(元) / limit_up_time(HH:MM)，"
    "合并回填当日 limit_up 池行（不插入新行、不覆盖主源其他列）",
    static={"pool_type": "limit_up"},
    fields=(
        FieldMap("code", "thscode", "normalize_code"),
        FieldMap("name", "name", "to_str"),
        FieldMap("continue_days", "continue_day_cnt", "to_int"),
        FieldMap("limit_up_time", "limit_up_time", "hhmm_from_str", required=False),
        FieldMap("seal_amount_yuan", "seal_money", "to_float"),
        FieldMap("max_seal_amount_yuan", "max_seal_money", "to_float", required=False),
        FieldMap("open_times", "break_limit_up_times", "to_int", required=False),
        FieldMap("turnover_rate", "turnover_ratio", "ratio_passthrough", required=False),
        FieldMap("amount_yuan", "amount", "to_float", required=False),
        FieldMap("market_cap_yuan", "market_cap", "to_float", required=False),
    ),
)

_LADDER_ROW = CapabilityMapping(
    source_id="hithink",
    capability="ladder",
    record_path="data.item[].boards.*[]",
    notes="连板天梯：data.item[] 每天一项（date + boards 六分组），摊平为「一票一行」；"
    "trade_date 由 ^date 取自上级 item，continue_days 取自 board_num",
    fields=(
        FieldMap("trade_date", "^date", "str_to_date"),
        FieldMap("code", "thscode", "normalize_code"),
        FieldMap("name", "name", "to_str"),
        FieldMap("continue_days", "board_num", "to_int"),
        FieldMap("first_seal_time", "first_seal_time", "hhmm_from_str", required=False),
    ),
)

_TRADING_DAY = CapabilityMapping(
    source_id="hithink",
    capability="trading_calendar",
    record_path="data.item",
    notes="交易日历：date(YYYYMMDD)；该端点仅返回交易日，语义上 is_open 恒为 True",
    fields=(
        FieldMap("trade_date", "date", "str_to_date"),
        FieldMap("is_open", None, "to_bool", default=True),
    ),
)

#: 同花顺全部能力的映射。
HITHINK_MAPPINGS: tuple[CapabilityMapping, ...] = (
    _DAILY_BAR,
    _LIMIT_UP_STOCK,
    _LIMIT_UP_SUPPLEMENT,
    _LADDER_ROW,
    _TRADING_DAY,
)


def register_hithink_mappings() -> None:
    """把同花顺映射注册进映射注册表（幂等）。"""
    for mapping in HITHINK_MAPPINGS:
        register_mapping(mapping)
