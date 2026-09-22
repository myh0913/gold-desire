"""选股通（xuangutong）各能力的声明式字段映射。

源字段口径（对齐 flash-api / baoer-api 端点实测响应，样例见 ``/tmp/ds_probe_out.json``）：

- 涨停池 ``/api/pool/detail``：``data[]``；``symbol`` 形如 ``600847.SS``（``.SS`` 由
  ``normalize_code`` 归一为 ``.SH``）、``limit_up_days`` 连板数、``break_limit_up_times``
  炸板次数、``turnover_ratio`` 已是小数、``total_capital`` 已是元、``first_limit_up`` 为
  **秒级**时间戳；**该端点不返回封单金额与成交额**，对应字段留空；
- 市场情绪 ``/api/market_indicator/line``：``data`` 为当日**分钟点列表**（243 条），
  取最后一个（收盘）点；``timestamp`` 为**秒级**；``limit_up_broken_ratio`` /
  ``yesterday_limit_up_avg_pcp`` 已是小数；``lianbangaodu`` 为 ``{连板数: 家数}`` 字典，
  其最大键即「最高连板高度」；**该源无情绪阶段名**，``stage`` 留空（由上层状态机派生）；
- 题材排名 ``/api/surge_stock/plates``：``data.items[] = {id, name, description}``，
  **数组顺序即排名**，故 ``rank`` 由记录序号派生；``trade_date`` 取自取数参数（上下文）；
  该源无核心股涨幅/数量，留空；
- 题材个股 ``/api/surge_stock/stocks``：``data.fields``（动态表头）+ ``data.items``
  （二维行数组），按 ``plates[]`` 展开为「一题材一行」；``cur_price`` 为浮点、
  ``px_change_rate`` / ``turnover_ratio`` 已是小数、``m_days_n_boards`` 为 ``"2天2板"``
  这类文本（空串表示非连板）；``trade_date`` 取自取数参数（上下文）；
- 快讯 ``/api/v6/message/newsflash``：``data.messages[]``，``created_at`` 为**秒级**
  时间戳、``impact`` 为整数级别、关联证券在 ``stocks[].symbol``（源**无** ``stock_symbols``）、
  ``subj_ids`` 为分类 id 列表。

缺失语义：契约必需字段用 ``REQUIRED``（缺失即拒绝，绝不填 0/空串）；上游不提供或本身可空的
字段用 ``required=False`` → ``None``（「能算的算、算不出的留空」），本就在契约上允许空串的
字段（如快讯 ``summary``）用 ``default=""``。
"""

from __future__ import annotations

from app.datasources.mappings.dsl import CapabilityMapping, FieldMap
from app.datasources.mappings.registry import register_mapping

__all__ = ["XUANGUTONG_MAPPINGS", "register_xuangutong_mappings"]

_MARKET_SENTIMENT = CapabilityMapping(
    source_id="xuangutong",
    capability="market_sentiment",
    record_path="data[-1]",
    notes="情绪：data 为当日分钟点列表，取最后一个（收盘）点；timestamp(秒) / "
    "market_temperature / limit_* / rise_count / fall_count / lianbangaodu(字典取最大键)；"
    "该源无情绪阶段名（stage 留空，由状态机派生）",
    fields=(
        FieldMap("trade_date", "timestamp", "sec_to_date"),
        FieldMap("temperature", "market_temperature", "to_float"),
        FieldMap("stage", "stage", "to_str", required=False),
        FieldMap("limit_up_count", "limit_up_count", "to_int"),
        FieldMap("limit_down_count", "limit_down_count", "to_int"),
        FieldMap("broken_board_count", "limit_up_broken_count", "to_int"),
        FieldMap("broken_rate", "limit_up_broken_ratio", "ratio_passthrough"),
        FieldMap("up_count", "rise_count", "to_int"),
        FieldMap("down_count", "fall_count", "to_int"),
        FieldMap("max_continue_days", "lianbangaodu", "max_key_int"),
        FieldMap("premium_rate", "yesterday_limit_up_avg_pcp", "ratio_passthrough"),
    ),
)

_LIMIT_UP_STOCK = CapabilityMapping(
    source_id="xuangutong",
    capability="limit_up_pool",
    record_path="data",
    notes="涨停池（7 种池型由取数参数 pool_name 决定，见 tasks.POOL_TYPES）："
    "symbol(.SS) / stock_chi_name / limit_up_days / break_limit_up_times / "
    "turnover_ratio(小数) / total_capital(总市值) / non_restricted_capital(流通市值) / "
    "first_limit_up(秒) / price(现价) / change_percent(涨幅,小数) / volume_bias_ratio(量比) / "
    "buy_lock_volume_ratio(封单比) / surge_reason{stock_reason, related_plates} / "
    "limit_timeline.items(封板时间线)；封单金额与成交额该端点不提供（留空）",
    fields=(
        # pool_type 取自取数参数：一个任务对 7 种池型逐轮取数，各轮落各自池型。
        # 缺省 limit_up 与 provider 的 URL 默认值保持一致（未显式传池型时视作涨停池）。
        FieldMap(
            "pool_type", None, "to_str", context="args.pool_name", default="limit_up", required=False
        ),
        FieldMap("code", "symbol", "normalize_code"),
        FieldMap("name", "stock_chi_name", "to_str"),
        FieldMap("continue_days", "limit_up_days", "to_int"),
        FieldMap("limit_up_time", "first_limit_up", "hhmm_from_sec", required=False),
        FieldMap("seal_amount_yuan", "seal_money", "to_float", required=False),
        FieldMap("open_times", "break_limit_up_times", "to_int", required=False),
        FieldMap("turnover_rate", "turnover_ratio", "ratio_passthrough"),
        FieldMap("amount_yuan", "amount", "to_float", required=False),
        FieldMap("market_cap_yuan", "total_capital", "to_float"),
        FieldMap("price", "price", "to_float", required=False),
        FieldMap("change_pct", "change_percent", "ratio_passthrough", required=False),
        FieldMap("volume_bias_ratio", "volume_bias_ratio", "to_float", required=False),
        FieldMap("free_cap_yuan", "non_restricted_capital", "to_float", required=False),
        FieldMap("seal_ratio", "buy_lock_volume_ratio", "to_float", required=False),
        FieldMap("reason", "surge_reason.stock_reason", "to_str", required=False),
        FieldMap("plates", "surge_reason.related_plates", "identity", required=False),
        FieldMap("timeline", "limit_timeline.items", "identity", required=False),
        FieldMap("list_date", "listed_date", "sec_to_date", required=False),
    ),
)

_THEME_RANK = CapabilityMapping(
    source_id="xuangutong",
    capability="theme_rank",
    record_path="data.items",
    notes="题材排名：data.items 数组顺序即排名（rank 由序号派生）；"
    "trade_date 取自取数参数（上下文）；核心涨幅由 provider 另打 plate/data 补入 "
    "`core_avg_pcp`（**已是小数口径**，故用 ratio_passthrough 而非 pct_to_ratio）；"
    "该源不提供核心股数量，core_count 由成分股聚合回填",
    fields=(
        FieldMap("trade_date", None, "str_to_date", context="args.date"),
        FieldMap("rank", "@index", "rank_from_index"),
        FieldMap("name", "name", "to_str"),
        FieldMap("core_avg_pct", "core_avg_pcp", "ratio_passthrough", required=False),
        FieldMap("description", "description", "to_str", required=False),
        FieldMap("core_count", "core_count", "to_int", required=False),
    ),
)

_THEME_STOCK = CapabilityMapping(
    source_id="xuangutong",
    capability="theme_stocks",
    record_path="data.items",
    columns_path="data.fields",
    explode_path="plates[]",
    notes="题材个股：表头 data.fields + 二维行数组 data.items；按 plates[] 展开"
    "（一只个股可属多个题材），父行字段用 ^ 回取；"
    "trade_date 取自取数参数（上下文）",
    fields=(
        FieldMap("trade_date", None, "str_to_date", context="args.date"),
        FieldMap("theme_name", "name", "to_str"),
        FieldMap("code", "^code", "normalize_code"),
        FieldMap("name", "^prod_name", "to_str"),
        FieldMap("price", "^cur_price", "to_float"),
        FieldMap("pct", "^px_change_rate", "ratio_passthrough"),
        FieldMap("turnover_rate", "^turnover_ratio", "ratio_passthrough"),
        FieldMap("continue_days", "^m_days_n_boards", "boards_from_text", required=False),
    ),
)

_NEWS_FLASH = CapabilityMapping(
    source_id="xuangutong",
    capability="newsflash",
    record_path="data.messages",
    notes="快讯：created_at(秒) / stocks[].symbol 关联证券 / subj_ids 分类；"
    "摘要常为空串（保持空串而非拒绝记录）。源不提供重要级别（impact 实测恒为 0），不映射",
    fields=(
        FieldMap("ts", "created_at", "sec_to_datetime"),
        FieldMap("title", "title", "to_str"),
        FieldMap("summary", "summary", "to_str", default=""),
        FieldMap("symbols", "stocks[].symbol", "list_of_str", required=False),
        FieldMap("categories", "subj_ids", "list_of_str", required=False),
    ),
)

#: 选股通全部能力的映射。
XUANGUTONG_MAPPINGS: tuple[CapabilityMapping, ...] = (
    _MARKET_SENTIMENT,
    _LIMIT_UP_STOCK,
    _THEME_RANK,
    _THEME_STOCK,
    _NEWS_FLASH,
)


def register_xuangutong_mappings() -> None:
    """把选股通映射注册进映射注册表（幂等）。"""
    for mapping in XUANGUTONG_MAPPINGS:
        register_mapping(mapping)
