"""东方财富监管名单数据源（交易所重点监控 / 异常波动）。

页面：https://vipmoney.eastmoney.com/collect/min_data/point_stock_monitor/index.html

**为什么是两个能力而不是一个**：本仓库的映射注册表以 ``(source_id, capability)``
为唯一键（见 ``mappings/registry.py``），且 ``docs/extend-datasource.md`` 明确规定
provider **不得**做字段归一化、mapping **不得**写条件分支。下面两个端点的响应形状
（裸数组 vs 数据中心信封）与字段名完全不同，无法用同一份声明式映射覆盖——故按
「一个能力 = 一种响应形状」拆成两个能力，**共用同一份契约** ``MonitorStockContract``
（两者产出的是同一个领域对象，只是取数路径不同）。这与 ``theme_rank`` /
``theme_stocks`` 用两个能力描述同一业务域是同一手法。

两个端点（2026-09-22 实测，均 HTTP 200，**都不需要 key**）：

- **重点监控证券**（能力 ``monitor_stocks``）
  ``mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json``
  —— 返回**裸 JSON 数组**（无 ``code``/``data`` 信封），最新名单、**无分页无历史**；
  实测 15 条；字段 ``STKCODE`` / ``STKNAME`` / ``MARKET``（``1``=沪 ``0``=深 ``B``=北）
  / ``VALIDATESTARTDATE`` / ``VALIDATEENDDATE`` / ``LINK_URL``（部分条目没有）。
- **异常波动**（能力 ``monitor_unusual``）
  ``datacenter.eastmoney.com/securities/api/data/v1/get``
  —— ``reportName=RPT_APP_UNUSUALBASIC``，``filter=(UNUSUAL_TYPE="002")`` 为严重异常波动
  （实测 179 条 / 4 页）、``"001"`` 为普通异常波动（实测 **5499 条 / 110 页**，历史很长）。
  信封 ``{code, message, result: {count, data, pages}, success, version}``，
  ``code=0 & success=true`` 为成功；按 ``NOTICE_DATE,END_DATE`` **倒序**返回，故首页即最新。

约束：未声明商业授权，仅低频研究用途；必须带移动端 UA + Referer
（``.env`` 里预留的 ``EASTMONEY_API_KEY`` 实测不参与鉴权，故本 provider 不读它）。
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Final

import httpx

from app.core.errors import UpstreamError
from app.datasources.base import BaseProvider, SourceKind, request_with_retry
from app.datasources.registry import register_provider

logger = logging.getLogger(__name__)

__all__ = [
    "DATACENTER_URL",
    "MOBCONFIG_BASE_URL",
    "MONITOR_REPORT_NAME",
    "UNUSUAL_COLUMNS",
    "UNUSUAL_TYPES",
    "EastmoneyProvider",
]

#: 重点监控名单基址（``/emcfg/stock_monitor.json`` 为裸数组）。
MOBCONFIG_BASE_URL: Final[str] = "https://mobappconfig.securities.eastmoney.com"
#: 数据中心通用取数端点（异常波动走这里）。
DATACENTER_URL: Final[str] = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
#: 异常波动报表名。
MONITOR_REPORT_NAME: Final[str] = "RPT_APP_UNUSUALBASIC"

#: 移动端 UA + Referer：该站点按 Referer 归属站点鉴权，缺一即被拒。
_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
)
_REFERER: Final[str] = (
    "https://vipmoney.eastmoney.com/collect/min_data/point_stock_monitor/index.html"
)

#: 异常波动请求列（对齐参考实现 ``quant_system.datasource.eastmoney``）。
#: ``PREDICT_*`` / ``IS_HIS`` 仅请求、**不落库**——保留它们是为了让 ``raw_responses``
#: 留档完整（排障/回放时有原始依据），契约与表结构不需要这两个冗余维度。
UNUSUAL_COLUMNS: Final[str] = (
    "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,UNUSUAL_TYPE,START_DATE,END_DATE,"
    "INFO_CODE,NOTICE_DATE,UNUSUAL_REASON,UNUSUAL_REASON_TYPE,MRAKET_TYPE,"
    "PREDICT_START_DATE,PREDICT_END_DATE,IS_HIS"
)

#: 契约 ``kind`` → 上游 ``UNUSUAL_TYPE``（``restricted`` 不走本端点，故不在表内）。
UNUSUAL_TYPES: Final[dict[str, str]] = {"severe": "002", "unusual": "001"}

#: 异常波动每页条数（上游上限 50）。
UNUSUAL_PAGE_SIZE: Final[int] = 50

#: 数据中心信封的成功业务码。
_SUCCESS_CODE: Final[int] = 0


def _make_client() -> httpx.AsyncClient:
    """构造默认 HTTP 客户端（测试 monkeypatch 本函数注入离线 transport）。"""
    return httpx.AsyncClient(timeout=20.0)


@register_provider
class EastmoneyProvider(BaseProvider):
    """东方财富监管名单数据源（重点监控 + 异常波动两个端点）。"""

    source_id: ClassVar[str] = "eastmoney"
    label: ClassVar[str] = "东方财富"
    kind: ClassVar[SourceKind] = SourceKind.HTTP
    capabilities: ClassVar[tuple[str, ...]] = ("monitor_stocks", "monitor_unusual")
    #: 公共站点，限流取保守值（低频研究用途）。
    rate_limit_per_min: ClassVar[int] = 60
    #: 三源之中最低优先（eltdx/hithink=10、xuangutong=20）。
    priority: ClassVar[int] = 30

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        mobconfig_base_url: str | None = None,
        datacenter_url: str | None = None,
    ) -> None:
        """初始化。

        Args:
            client: 复用的 HTTP 客户端；``None`` 时每次 fetch 惰性新建。
            mobconfig_base_url: 重点监控基址覆盖（测试用）。
            datacenter_url: 数据中心端点覆盖（测试用）。
        """
        self._client = client
        self._mobconfig = (mobconfig_base_url or MOBCONFIG_BASE_URL).rstrip("/")
        self._datacenter = datacenter_url or DATACENTER_URL

    # ------------------------------------------------------------ 内部

    def _headers(self) -> dict[str, str]:
        """公共请求头（UA + Referer，二者缺一即被站点拒绝）。"""
        return {"User-Agent": _USER_AGENT, "Referer": _REFERER}

    def _route(
        self, capability: str, args: dict[str, Any]
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        """把能力解析为 ``(url, params, headers)``。

        - ``monitor_stocks``（重点监控）→ mobappconfig 裸数组端点，无参数、无分页；
        - ``monitor_unusual``（异常波动）→ 数据中心端点，``kind`` 决定 ``UNUSUAL_TYPE``
          （``severe``→002 / ``unusual``→001），``page`` 缺省第 1 页。
        """
        if capability == "monitor_stocks":
            return self._mobconfig + "/emcfg/stock_monitor.json", {}, self._headers()
        if capability == "monitor_unusual":
            kind = str(args.get("kind", "severe"))
            unusual_type = UNUSUAL_TYPES.get(kind)
            if unusual_type is None:
                raise UpstreamError(
                    f"eastmoney 异常波动不支持类型 {kind!r}（仅 severe / unusual）",
                    detail={"source": self.source_id, "kind": kind},
                )
            params: dict[str, Any] = {
                "reportName": MONITOR_REPORT_NAME,
                "columns": UNUSUAL_COLUMNS,
                "filter": f'(UNUSUAL_TYPE="{unusual_type}")',
                "sortColumns": "NOTICE_DATE,END_DATE",
                "sortTypes": "-1,-1",
                "pageNumber": int(args.get("page", 1)),
                "pageSize": UNUSUAL_PAGE_SIZE,
                "source": "SECURITIES",
                "client": "APP",
            }
            return self._datacenter, params, self._headers()
        raise UpstreamError(
            f"eastmoney 不支持能力 {capability!r}",
            detail={"source": self.source_id, "capability": capability},
        )

    def _unwrap_list(self, body: Any, capability: str) -> dict[str, Any]:
        """校验**裸数组**响应并套一层 ``{"data": [...]}``。

        本仓库的 payload 一律是对象（``RawPayload.payload`` / ``raw_responses`` /
        映射 ``record_path`` 都按对象设计），而该端点直接返回数组，故在此套一层薄信封
        ——**只包不改**：不重命名、不增删任何上游字段，映射侧用 ``record_path="data"``
        消费（字段归一化仍在 mapping 层完成）。
        """
        if not isinstance(body, list):
            raise UpstreamError(
                f"eastmoney {capability} 响应体不是数组",
                detail={
                    "source": self.source_id,
                    "capability": capability,
                    "type": type(body).__name__,
                },
            )
        return {"data": body}

    def _unwrap_datacenter(self, body: Any, capability: str) -> dict[str, Any]:
        """校验数据中心信封，**原样**返回（保留 ``result.count`` 供排障）。"""
        if not isinstance(body, dict):
            raise UpstreamError(
                f"eastmoney {capability} 响应体不是对象",
                detail={
                    "source": self.source_id,
                    "capability": capability,
                    "type": type(body).__name__,
                },
            )
        code = body.get("code")
        if code != _SUCCESS_CODE or not body.get("success"):
            raise UpstreamError(
                f"eastmoney {capability} 业务错误 code={code}"
                f" message={body.get('message', '')!s:.120}",
                detail={
                    "source": self.source_id,
                    "capability": capability,
                    "code": code,
                    "message": body.get("message"),
                },
            )
        if not isinstance(body.get("result"), dict):
            raise UpstreamError(
                f"eastmoney {capability} 响应缺少 result",
                detail={"source": self.source_id, "capability": capability},
            )
        return body

    # ------------------------------------------------------------ fetch

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        """按能力取回**原始**上游 payload（仅做信封校验与裸数组套壳）。

        Raises:
            UpstreamError: 请求失败、业务码异常或响应形状不符。
        """
        url, params, headers = self._route(capability, args)
        client = self._client
        owns = client is None
        if client is None:
            client = _make_client()
        try:
            response = await request_with_retry(
                client,
                "GET",
                url,
                source=self.source_id,
                params=params,
                headers=headers,
            )
        finally:
            if owns:
                await client.aclose()
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError(
                f"eastmoney {capability} 返回非 JSON 响应",
                detail={"source": self.source_id, "capability": capability},
            ) from exc
        if capability == "monitor_stocks":
            return self._unwrap_list(body, capability)
        return self._unwrap_datacenter(body, capability)
