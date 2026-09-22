"""假数据源（fake）的声明式字段映射。

刻意使用与契约**不同的源字段名与单位**（symbol / limit_up_days / vol 手 /
amount 万元 / ms 时间戳），以确保映射层被真实地走通——这也是「新增源只加一份
映射」范式的活样例。
"""

from __future__ import annotations

from app.datasources.mappings.dsl import CapabilityMapping, FieldMap
from app.datasources.mappings.registry import register_mapping

__all__ = ["FAKE_MAPPINGS", "register_fake_mappings"]

_DAILY_BAR = CapabilityMapping(
    source_id="fake",
    capability="daily_bars",
    record_path="result.rows",
    notes="日线：symbol(.SS) / date_ms / vol(手) / amount(万元)",
    fields=(
        FieldMap("code", "symbol", "normalize_code"),
        FieldMap("trade_date", "date_ms", "ms_to_date"),
        FieldMap("open", "o", "to_float"),
        FieldMap("high", "h", "to_float"),
        FieldMap("low", "l", "to_float"),
        FieldMap("close", "c", "to_float"),
        FieldMap("pre_close", "pc", "to_float"),
        FieldMap("volume_shares", "vol", "lots_to_shares"),
        FieldMap("amount_yuan", "amount", "yuan_from_wan"),
    ),
)

_LIMIT_UP_STOCK = CapabilityMapping(
    source_id="fake",
    capability="limit_up_pool",
    record_path="data.items",
    notes="涨停池：stock_chi_name / limit_up_days / seal_money(万元) / turnover(%) / mkt_cap(亿元)",
    static={"pool_type": "limit_up"},
    fields=(
        FieldMap("code", "symbol", "normalize_code"),
        FieldMap("name", "stock_chi_name", "to_str"),
        FieldMap("continue_days", "limit_up_days", "to_int"),
        FieldMap("limit_up_time", "first_limit_up_time_ms", "hhmm_from_ms"),
        FieldMap("seal_amount_yuan", "seal_money", "yuan_from_wan"),
        FieldMap("open_times", "open_cnt", "to_int"),
        FieldMap("turnover_rate", "turnover", "pct_to_ratio"),
        FieldMap("amount_yuan", "amount", "yuan_from_wan"),
        FieldMap("market_cap_yuan", "mkt_cap", "yuan_from_yi"),
    ),
)

#: 涨停池补数轮（fake 兜底）：与 hithink supplement 同形状（thscode / seal_money 元）。
_FAKE_LIMIT_UP_SUPPLEMENT = CapabilityMapping(
    source_id="fake",
    capability="limit_up_pool_supplement",
    record_path="data.item",
    notes="涨停池补数（fake）：thscode / seal_money(元) / max_seal_money(元) / limit_up_time",
    static={"pool_type": "limit_up"},
    fields=(
        FieldMap("code", "thscode", "normalize_code"),
        FieldMap("name", "name", "to_str"),
        FieldMap("continue_days", "continue_day_cnt", "to_int"),
        FieldMap("limit_up_time", "limit_up_time", "hhmm_from_str", required=False),
        FieldMap("seal_amount_yuan", "seal_money", "to_float"),
        FieldMap("max_seal_amount_yuan", "max_seal_money", "to_float", required=False),
    ),
)

_LADDER_ROW = CapabilityMapping(
    source_id="fake",
    capability="ladder",
    record_path="matrix",
    notes="连板天梯：trade_day(YYYYMMDD) / board_cnt / first_seal_ms",
    fields=(
        FieldMap("trade_date", "trade_day", "str_to_date"),
        FieldMap("code", "symbol", "normalize_code"),
        FieldMap("name", "name", "to_str"),
        FieldMap("continue_days", "board_cnt", "to_int"),
        FieldMap("first_seal_time", "first_seal_ms", "hhmm_from_ms"),
    ),
)

_TRADING_DAY = CapabilityMapping(
    source_id="fake",
    capability="trading_calendar",
    record_path="days",
    notes="交易日历：cal_date(YYYYMMDD) / open_flag(1/0)",
    fields=(
        FieldMap("trade_date", "cal_date", "str_to_date"),
        FieldMap("is_open", "open_flag", "to_bool"),
    ),
)

_MARKET_SENTIMENT = CapabilityMapping(
    source_id="fake",
    capability="market_sentiment",
    record_path="data",
    notes="情绪：up_limit/down_limit/broken_pct(%)/premium(%)/max_board",
    fields=(
        FieldMap("trade_date", "trade_day", "str_to_date"),
        FieldMap("temperature", "temperature", "to_float"),
        FieldMap("stage", "stage", "to_str"),
        FieldMap("limit_up_count", "up_limit", "to_int"),
        FieldMap("limit_down_count", "down_limit", "to_int"),
        FieldMap("broken_board_count", "broken", "to_int"),
        FieldMap("broken_rate", "broken_pct", "pct_to_ratio"),
        FieldMap("up_count", "rise", "to_int"),
        FieldMap("down_count", "fall", "to_int"),
        FieldMap("max_continue_days", "max_board", "to_int"),
        FieldMap("premium_rate", "premium", "pct_to_ratio"),
    ),
)

_THEME_RANK = CapabilityMapping(
    source_id="fake",
    capability="theme_rank",
    record_path="plates",
    notes="题材排名：rank_no / plate_name / core_avg(%) / core_cnt",
    fields=(
        FieldMap("trade_date", "trade_day", "str_to_date"),
        FieldMap("rank", "rank_no", "to_int"),
        FieldMap("name", "plate_name", "to_str"),
        FieldMap("core_avg_pct", "core_avg", "pct_to_ratio"),
        FieldMap("description", "desc", "to_str"),
        FieldMap("core_count", "core_cnt", "to_int"),
    ),
)

_THEME_STOCK = CapabilityMapping(
    source_id="fake",
    capability="theme_stocks",
    record_path="stocks",
    notes="题材个股：stock_name / last / pct_chg(%) / turnover(%) / days",
    fields=(
        FieldMap("trade_date", "trade_day", "str_to_date"),
        FieldMap("theme_name", "plate_name", "to_str"),
        FieldMap("code", "symbol", "normalize_code"),
        FieldMap("name", "stock_name", "to_str"),
        FieldMap("price", "last", "to_float"),
        FieldMap("pct", "pct_chg", "pct_to_ratio"),
        FieldMap("turnover_rate", "turnover", "pct_to_ratio"),
        FieldMap("continue_days", "days", "to_int"),
    ),
)

_NEWS_FLASH = CapabilityMapping(
    source_id="fake",
    capability="newsflash",
    record_path="messages",
    notes="快讯：created_at_ms / digest / stock_symbols / tags",
    fields=(
        FieldMap("ts", "created_at_ms", "ms_to_datetime"),
        FieldMap("title", "title", "to_str"),
        FieldMap("summary", "digest", "to_str"),
        FieldMap("symbols", "stock_symbols", "list_of_str"),
        FieldMap("categories", "tags", "list_of_str"),
    ),
)

_FAKE_MINUTE_BAR = CapabilityMapping(
    source_id="fake",
    capability="minute_bars",
    record_path="points",
    notes="分时分钟点（fake）：time_label / price / volume(手)；minute_index 派生自序号",
    fields=(
        FieldMap("code", None, "identity", context="args.thscode"),
        FieldMap("trade_date", None, "str_to_date", context="args.date"),
        FieldMap("minute_index", "@index", "to_int"),
        FieldMap("time_label", "time_label", "to_str"),
        FieldMap("price", "price", "to_float"),
        FieldMap("volume_lots", "volume", "to_int"),
        FieldMap("amount_yuan", "amount", "to_float", required=False),
    ),
)

_FAKE_OPENING_MATCH = CapabilityMapping(
    source_id="fake",
    capability="opening_match",
    record_path="matches",
    notes="09:25 撮合（fake）：price / volume(手) / time_label",
    fields=(
        FieldMap("code", None, "identity", context="args.thscode"),
        FieldMap("trade_date", None, "str_to_date", context="args.date"),
        FieldMap("price", "price", "to_float"),
        FieldMap("volume_lots", "volume", "to_int", required=False),
        FieldMap("time_label", "time_label", "to_str", required=False),
    ),
)

_FAKE_MONITOR_RESTRICTED = CapabilityMapping(
    source_id="fake",
    capability="monitor_stocks",
    record_path="data",
    static={"kind": "restricted"},
    notes="重点监控（fake）：STKCODE + MARKET 双列拼标准代码；VALIDATE* 为监控有效期",
    fields=(
        FieldMap("trade_date", None, "str_to_date", context="args.date"),
        FieldMap("code", ("STKCODE", "MARKET"), "market_code"),
        FieldMap("name", "STKNAME", "to_str"),
        FieldMap("start_date", "VALIDATESTARTDATE", "str_to_date", required=False),
        FieldMap("end_date", "VALIDATEENDDATE", "str_to_date", required=False),
        FieldMap("link_url", "LINK_URL", "to_str", required=False),
    ),
)

_FAKE_MONITOR_UNUSUAL = CapabilityMapping(
    source_id="fake",
    capability="monitor_unusual",
    record_path="result.data",
    notes="异常波动（fake）：SECURITY_CODE + MRAKET_TYPE 双列拼标准代码；"
    "START_DATE / END_DATE / NOTICE_DATE 为 datetime 字符串（str_to_date 兼容）",
    fields=(
        FieldMap("trade_date", None, "str_to_date", context="args.date"),
        FieldMap("kind", None, "to_str", context="args.kind"),
        FieldMap("code", ("SECURITY_CODE", "MRAKET_TYPE"), "market_code"),
        FieldMap("name", "SECURITY_NAME_ABBR", "to_str"),
        FieldMap("reason", "UNUSUAL_REASON", "to_str", required=False),
        FieldMap("start_date", "START_DATE", "str_to_date", required=False),
        FieldMap("end_date", "END_DATE", "str_to_date", required=False),
        FieldMap("notice_date", "NOTICE_DATE", "str_to_date", required=False),
        FieldMap("info_code", "INFO_CODE", "to_str", required=False),
        FieldMap("reason_type", "UNUSUAL_REASON_TYPE", "to_str", required=False),
    ),
)

#: fake 源覆盖全部 Phase-1 能力的映射。
FAKE_MAPPINGS: tuple[CapabilityMapping, ...] = (
    _DAILY_BAR,
    _LIMIT_UP_STOCK,
    _FAKE_LIMIT_UP_SUPPLEMENT,
    _LADDER_ROW,
    _TRADING_DAY,
    _MARKET_SENTIMENT,
    _THEME_RANK,
    _THEME_STOCK,
    _NEWS_FLASH,
    _FAKE_MINUTE_BAR,
    _FAKE_OPENING_MATCH,
    _FAKE_MONITOR_RESTRICTED,
    _FAKE_MONITOR_UNUSUAL,
)


def register_fake_mappings() -> None:
    """把 fake 映射注册进映射注册表（幂等）。"""
    for mapping in FAKE_MAPPINGS:
        register_mapping(mapping)
