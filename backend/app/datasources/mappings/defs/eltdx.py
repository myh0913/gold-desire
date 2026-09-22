"""eltdx（通达信行情，TDX TCP）数据源映射：分时分钟点 / 09:25 集合竞价撮合。

口径对齐旧项目 ``quant-system``（分时唯一源为 eltdx，无 HTTP 备源）：

- ``minute_bars``：payload ``{"points": [{time_label, price, volume}, ...]}``，
  240 点/日（09:31~11:30 → 0..119，13:01~15:00 → 120..239）；
  ``time_label`` 零填充 ``"HH:MM"``；``volume`` 单位**手**（分钟增量）；
  ``minute_index`` 由记录序号（``@index``）派生。
- ``opening_match``：payload ``{"match": {price, volume, time_label}}``，
  09:25 正式撮合价即当日开盘价（旧系统 ``dragon`` 盘中分类的数据源）。
"""

from __future__ import annotations

from app.datasources.mappings.dsl import CapabilityMapping, FieldMap
from app.datasources.mappings.registry import register_mapping

_MINUTE_BAR = CapabilityMapping(
    source_id="eltdx",
    capability="minute_bars",
    record_path="points",
    notes="分时分钟点：time_label 零填充 HH:MM / price 元 / volume 手（分钟增量）；"
    "minute_index 由记录序号派生；amount_yuan 来自 1m K 线（provider 按 time_label "
    "对齐附加，K 线缺失时为 None）",
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

_OPENING_MATCH = CapabilityMapping(
    source_id="eltdx",
    capability="opening_match",
    record_path="matches",
    notes="09:25 正式撮合：price 即当日开盘价；volume 手；无撮合数据时 provider "
    "返回 matches=[] → 0 条记录（按成功 0 行处理）",
    fields=(
        FieldMap("code", None, "identity", context="args.thscode"),
        FieldMap("trade_date", None, "str_to_date", context="args.date"),
        FieldMap("price", "price", "to_float"),
        FieldMap("volume_lots", "volume", "to_int", required=False),
        FieldMap("time_label", "time_label", "to_str", required=False),
    ),
)

#: eltdx 源全部能力的映射。
ELTDX_MAPPINGS: tuple[CapabilityMapping, ...] = (_MINUTE_BAR, _OPENING_MATCH)


def register_eltdx_mappings() -> None:
    """把 eltdx 映射注册进映射注册表（幂等）。"""
    for mapping in ELTDX_MAPPINGS:
        register_mapping(mapping)


register_eltdx_mappings()
