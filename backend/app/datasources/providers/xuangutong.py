"""选股通（xuangubao / xuangutong）数据源：HTTP provider。

对齐只读参考实现 ``quant-system/src/quant_system/datasource/xuangutong.py``。

能力 / 端点 / 参数 / 认证
-------------------------

- ``market_sentiment``：``GET {flash}/api/market_indicator/line``；参数 fields / date。
- ``limit_up_pool``：``GET {flash}/api/pool/detail``；参数 pool_name=limit_up / date（可选）。
- ``theme_rank``：``GET {flash}/api/surge_stock/plates``；无参数。
- ``theme_stocks``：``GET {flash}/api/surge_stock/stocks``；参数 normal / uplimit。
- ``newsflash``：``GET {baoer}/api/v6/message/newsflash``；参数 limit / subj_ids /
  has_explain / platform=pcweb。

其中 ``flash`` = ``https://flash-api.xuangubao.com.cn``、``baoer`` =
``https://baoer-api.xuangubao.com.cn``。``settings.xuangutong_base_url`` 是单值占位、
无法覆盖双域名，故这里用真实常量（可在构造时覆盖）。公共请求头：``User-Agent`` / ``Referer``。

**newsflash 额外要求** ``x-ivanka-token`` 请求头：取 ``settings.xuangutong_api_key``（gold-desire
现有的上游密钥占位；参考实现里该值来自 ``XUANGUTONG_IVANKA_TOKEN``）。该 token 为**可选**配置：
缺失时 :meth:`XuangutongProvider.fetch` 抛明确的 :class:`UpstreamError`，绝不 import 期崩溃。
- 业务码：``20000``（或 ``0``）成功；``50008`` 表示 token 无效，上抛 ``UpstreamError``。
- :meth:`XuangutongProvider.fetch` 只取回原始 payload，字段/单位归一交给
  ``mappings/defs/xuangutong.py``。
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Final

import httpx

from app.core.config import get_settings
from app.core.errors import UpstreamError
from app.datasources.base import BaseProvider, SourceKind, request_with_retry
from app.datasources.registry import register_provider

logger = logging.getLogger(__name__)

__all__ = ["BAOER_BASE_URL", "FLASH_BASE_URL", "HOME_URL", "XuangutongProvider"]

#: flash-api（情绪 / 涨停池 / 题材）。
FLASH_BASE_URL: Final[str] = "https://flash-api.xuangubao.com.cn"
#: baoer-api（快讯，需 token）。
BAOER_BASE_URL: Final[str] = "https://baoer-api.xuangubao.com.cn"
#: Referer 归属站点。
HOME_URL: Final[str] = "https://xuangutong.com.cn"

_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
)
_SUCCESS_CODES: Final[frozenset[int]] = frozenset({20000, 0})
_TOKEN_INVALID_CODE: Final[int] = 50008

#: 情绪指标请求字段（与参考实现一致）。
SENTIMENT_FIELDS: Final[str] = (
    "market_temperature,limit_up_count,limit_down_count,"
    "limit_up_broken_count,limit_up_broken_ratio,ziranzhangting_count,"
    "rise_count,fall_count,stay_count,yesterday_limit_up_avg_pcp,lianbangaodu"
)


def _make_client() -> httpx.AsyncClient:
    """构造默认 HTTP 客户端（测试 monkeypatch 本函数注入离线 transport）。"""
    return httpx.AsyncClient(timeout=20.0)


@register_provider
class XuangutongProvider(BaseProvider):
    """选股通 HTTP 数据源（情绪 / 涨停池 / 题材 / 快讯）。"""

    source_id: ClassVar[str] = "xuangutong"
    label: ClassVar[str] = "选股通"
    kind: ClassVar[SourceKind] = SourceKind.HTTP
    capabilities: ClassVar[tuple[str, ...]] = (
        "market_sentiment",
        "limit_up_pool",
        "theme_rank",
        "theme_stocks",
        "newsflash",
    )
    rate_limit_per_min: ClassVar[int] = 120
    priority: ClassVar[int] = 20

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        flash_base_url: str | None = None,
        baoer_base_url: str | None = None,
        token: str | None = None,
    ) -> None:
        """初始化。

        Args:
            client: 复用的 HTTP 客户端；``None`` 时每次 fetch 惰性新建。
            flash_base_url: flash-api 基址覆盖。
            baoer_base_url: baoer-api 基址覆盖。
            token: ``x-ivanka-token``（快讯用）；缺省读 ``settings.xuangutong_api_key``。
        """
        self._client = client
        self._flash = (flash_base_url or FLASH_BASE_URL).rstrip("/")
        self._baoer = (baoer_base_url or BAOER_BASE_URL).rstrip("/")
        self._token = token if token is not None else get_settings().xuangutong_api_key

    # ------------------------------------------------------------ 内部

    def _base_headers(self) -> dict[str, str]:
        """公共请求头。"""
        return {"User-Agent": _USER_AGENT, "Referer": HOME_URL + "/"}

    def _newsflash_headers(self) -> dict[str, str]:
        """快讯请求头：在公共头上附加 ``x-ivanka-token``；缺失即明确报错。"""
        if not self._token:
            raise UpstreamError(
                "xuangutong newsflash 未配置 x-ivanka-token（XUANGUTONG_API_KEY）",
                detail={"source": self.source_id, "setting": "xuangutong_api_key"},
            )
        return {**self._base_headers(), "x-ivanka-token": self._token}

    def _route(
        self, capability: str, args: dict[str, Any]
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        """把能力解析为 ``(url, params, headers)``。"""
        if capability == "market_sentiment":
            params: dict[str, Any] = {"fields": SENTIMENT_FIELDS}
            if args.get("date"):
                params["date"] = str(args["date"])
            return self._flash + "/api/market_indicator/line", params, self._base_headers()
        if capability == "limit_up_pool":
            params = {"pool_name": str(args.get("pool_name", "limit_up"))}
            if args.get("date"):
                params["date"] = str(args["date"])
            return self._flash + "/api/pool/detail", params, self._base_headers()
        if capability == "theme_rank":
            return self._flash + "/api/surge_stock/plates", {}, self._base_headers()
        if capability == "theme_stocks":
            params = {
                "normal": str(bool(args.get("normal", True))).lower(),
                "uplimit": str(bool(args.get("uplimit", True))).lower(),
            }
            return self._flash + "/api/surge_stock/stocks", params, self._base_headers()
        if capability == "newsflash":
            params = {
                "limit": int(args.get("limit", 20)),
                "subj_ids": int(args.get("subj_ids", 10)),
                "has_explain": str(bool(args.get("has_explain", False))).lower(),
                "platform": "pcweb",
            }
            return self._baoer + "/api/v6/message/newsflash", params, self._newsflash_headers()
        raise UpstreamError(
            f"xuangutong 不支持能力 {capability!r}",
            detail={"source": self.source_id, "capability": capability},
        )

    def _unwrap(self, response: httpx.Response, capability: str) -> dict[str, Any]:
        """解析响应体并校验业务码，返回**原始信封**。"""
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError(
                f"xuangutong {capability} 返回非 JSON 响应",
                detail={"source": self.source_id, "capability": capability},
            ) from exc
        if not isinstance(body, dict):
            raise UpstreamError(
                f"xuangutong {capability} 响应体不是对象",
                detail={"source": self.source_id, "capability": capability},
            )
        code = body.get("code")
        if code not in _SUCCESS_CODES:
            kind = "token 无效" if code == _TOKEN_INVALID_CODE else "业务错误"
            raise UpstreamError(
                f"xuangutong {capability} {kind} code={code}: {body.get('message', '')!s:.120}",
                detail={
                    "source": self.source_id,
                    "capability": capability,
                    "code": code,
                    "message": body.get("message"),
                },
            )
        return body

    # ------------------------------------------------------------ fetch

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        """取回该能力的**原始**上游 payload（含 ``code`` / ``message`` 信封）。

        Raises:
            UpstreamError: 快讯缺 token、业务码异常或请求失败。
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
            payload = self._unwrap(response, capability)
            # 注意：必须在 finally 之前补齐——自建 client 会在 finally 里关闭。
            if capability == "theme_rank":
                await self._enrich_theme_rank(client, payload, headers)
        finally:
            if owns:
                await client.aclose()
        return payload

    async def _enrich_theme_rank(
        self, client: httpx.AsyncClient, payload: dict[str, Any], headers: dict[str, str]
    ) -> None:
        """给题材榜补 ``core_avg_pcp``（核心股平均涨幅）。

        ``surge_stock/plates`` 只给题材名与说明，**核心涨幅要另打 ``plate/data``**
        （一次批量取全部 plate_id，避免 N 次请求）。补齐属可选增强：失败时打警告后
        原样返回，映射会把 ``core_avg_pct`` 留空，前端显示 ``--``——不阻断主采集。
        """
        data = payload.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            return
        ids = [
            str(item["id"])
            for item in items
            if isinstance(item, dict) and item.get("id") is not None
        ]
        if not ids:
            return
        try:
            response = await request_with_retry(
                client,
                "GET",
                self._flash + "/api/plate/data",
                source=self.source_id,
                params={"fields": "core_avg_pcp", "plates": ",".join(ids)},
                headers=headers,
            )
            body = self._unwrap(response, "plate_data")
        except (UpstreamError, httpx.HTTPError) as exc:
            logger.warning("xuangutong theme_rank 核心涨幅补齐失败，按缺失处理: %s", exc)
            return
        stats = body.get("data")
        if not isinstance(stats, dict):
            return
        by_id = {str(key): value for key, value in stats.items()}
        filled = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            stat = by_id.get(str(item.get("id")))
            if isinstance(stat, dict) and stat.get("core_avg_pcp") is not None:
                item["core_avg_pcp"] = stat["core_avg_pcp"]
                filled += 1
        logger.debug("xuangutong theme_rank 核心涨幅补齐 %d/%d", filled, len(items))
