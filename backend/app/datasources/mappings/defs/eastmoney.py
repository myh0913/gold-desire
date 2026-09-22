"""东方财富（eastmoney）监管名单的声明式字段映射。

两个端点形状不同，故是**两份**映射、两个能力，共用契约 ``MonitorStockContract``
（原因见 ``providers/eastmoney.py`` 模块文档）：

- ``monitor_stocks``：重点监控 ``/emcfg/stock_monitor.json``（provider 已把裸数组
  套成 ``{"data": [...]}``）；
- ``monitor_unusual``：异常波动 ``RPT_APP_UNUSUALBASIC``（``result.data``）。

源字段口径（2026-09-22 实测）：

**重点监控**
  ``STKCODE`` 为 6 位代码、``MARKET`` 为交易所标记（``1``=沪 / ``0``=深 / ``B``=北）、
  ``STKNAME`` 简称、``VALIDATESTARTDATE`` / ``VALIDATEENDDATE`` 为 ``YYYY-MM-DD``；
  ``LINK_URL`` 部分条目缺失。**标准代码必须由 ``STKCODE`` + ``MARKET`` 两列共同决定**
  ——该名单含 513390 / 159509 / 501225 等基金/ETF，仅凭首位数字推断交易所会判错
  （实测 15 条里错 9 条），故用多列源 ``("STKCODE", "MARKET")`` + ``market_code``。
  该端点**无**原因文本 / 公告日 / 公告编号 → 对应契约字段留空（不填 0/空串）。

**异常波动**
  ``SECUCODE`` 已是 ``000017.SZ`` 形制、``SECURITY_CODE`` 为 6 位代码、``MRAKET_TYPE``
  为中文交易所名（``上交所`` / ``深交所`` / ``北交所``，上游字段名确实拼作 MRAKET；实测
  非空且与 ``SECUCODE`` 后缀 100% 一致），``START_DATE`` / ``END_DATE`` /
  ``NOTICE_DATE`` 为 ``YYYY-MM-DD 00:00:00`` 形制（``str_to_date`` 已兼容），
  ``UNUSUAL_REASON`` 为原因全文、``UNUSUAL_REASON_TYPE`` 为原因分类、``INFO_CODE``
  为公告编号；``START_DATE`` 可为 ``null``。标准代码同样用多列源（
  ``("SECURITY_CODE", "MRAKET_TYPE")``）而非 ``SECUCODE`` 直取，理由同上：
  基金代码在数字推断规则下会失效，以交易所标注为准最稳。
  该端点**无**公告链接 → ``link_url`` 留空。

``trade_date`` 取自取数参数（上游响应不含日期）；``kind`` 在重点监控用 ``static``
常量注入，在异常波动取自取数参数（同一端点按 ``UNUSUAL_TYPE`` 分 severe / unusual）。
"""

from __future__ import annotations

from app.datasources.mappings.dsl import CapabilityMapping, FieldMap
from app.datasources.mappings.registry import register_mapping

__all__ = ["EASTMONEY_MAPPINGS", "register_eastmoney_mappings"]

_MONITOR_RESTRICTED = CapabilityMapping(
    source_id="eastmoney",
    capability="monitor_stocks",
    record_path="data",
    static={"kind": "restricted"},
    notes="重点监控：STKCODE + MARKET 双列拼标准代码（market_code）；"
    "VALIDATESTARTDATE / VALIDATEENDDATE 为监控有效期；LINK_URL 可缺；"
    "该端点无原因文本 / 公告日 / 公告编号，对应契约字段留空",
    fields=(
        FieldMap("trade_date", None, "str_to_date", context="args.date"),
        FieldMap("code", ("STKCODE", "MARKET"), "market_code"),
        FieldMap("name", "STKNAME", "to_str"),
        FieldMap("start_date", "VALIDATESTARTDATE", "str_to_date", required=False),
        FieldMap("end_date", "VALIDATEENDDATE", "str_to_date", required=False),
        FieldMap("link_url", "LINK_URL", "to_str", required=False),
    ),
)

_MONITOR_UNUSUAL = CapabilityMapping(
    source_id="eastmoney",
    capability="monitor_unusual",
    record_path="result.data",
    notes="异常波动：SECURITY_CODE + MRAKET_TYPE 双列拼标准代码；START_DATE / END_DATE "
    "为异动区间、NOTICE_DATE 公告日、INFO_CODE 公告编号、UNUSUAL_REASON 原因全文、"
    "UNUSUAL_REASON_TYPE 原因分类；START_DATE 可为 null；kind 取自取数参数；"
    "该端点无公告链接（link_url 留空）",
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

#: 东财源覆盖的映射（监管名单两个端点各一份）。
EASTMONEY_MAPPINGS: tuple[CapabilityMapping, ...] = (
    _MONITOR_RESTRICTED,
    _MONITOR_UNUSUAL,
)


def register_eastmoney_mappings() -> None:
    """把东财映射注册进映射注册表（幂等；每个能力各一份）。"""
    for mapping in EASTMONEY_MAPPINGS:
        register_mapping(mapping)
