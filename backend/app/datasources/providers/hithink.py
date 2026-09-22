"""同花顺（fuyao Financial API）数据源：HTTP provider。

对齐只读参考实现 ``quant-system/src/quant_system/datasource/hithink.py``。

能力 / 端点 / 参数 / 认证
-------------------------

- ``daily_bars``：``GET {base}/api/a-share/prices/historical``；参数 thscode / interval=1d /
  start(ms) / end(ms) / adjust=none。
- ``limit_up_pool``：``GET {base}/api/a-share/special-data/limit-up-pool``；参数 page / size /
  sort_field / sort_dir / date_ms（可选）。
- ``ladder``：``GET {base}/api/a-share/special-data/limit-up-ladder``；无参数。
- ``trading_calendar``：``GET {base}/api/a-share/calendar/trading-days``；无参数。

- 认证：请求头 ``X-api-key``（``settings.hithink_api_key`` / 环境变量 ``HITHINK_API_KEY``）。
- 业务码：``code=0`` 成功；``code=2001`` 密钥缺失/无效、``code=2003`` 无权访问，均上抛
  :class:`UpstreamError`（``detail`` 携带 code/message 与端点）。
- :meth:`HithinkProvider.fetch` **只取回原始 payload（含 ``code`` / ``message`` 信封）**，
  字段名与单位一律交给 ``mappings/defs/hithink.py`` 声明式映射，本层不做任何归一化。
- 限流与重试复用框架的 :class:`~app.datasources.base.TokenBucket`（``acquire``）与
  :func:`~app.datasources.base.request_with_retry`，不手写重试逻辑。
"""

from __future__ import annotations

from typing import Any, ClassVar, Final

import httpx

from app.core.config import get_settings
from app.core.errors import UpstreamError
from app.datasources.base import BaseProvider, SourceKind, request_with_retry
from app.datasources.registry import register_provider

__all__ = ["DEFAULT_BASE_URL", "HithinkProvider"]

#: 上游真实基址（见参考实现）。``settings.hithink_base_url`` 仍是占位值
#: （``https://api.hithink.example``），故默认走真实域名，可在构造时覆盖。
DEFAULT_BASE_URL: Final[str] = "https://fuyao.aicubes.cn"

_SUCCESS_CODE: Final[int] = 0
_AUTH_CODES: Final[frozenset[int]] = frozenset({2001, 2003})


def _make_client() -> httpx.AsyncClient:
    """构造默认 HTTP 客户端。

    测试通过 monkeypatch 本函数注入 ``httpx.MockTransport``，从而离线走通真实 fetch 代码路径。
    """
    return httpx.AsyncClient(timeout=30.0)


def _require_str(args: dict[str, Any], key: str) -> str:
    """取必填字符串参数，缺失即抛 ``UpstreamError``（不静默用空值）。"""
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UpstreamError(
            f"hithink 缺少必填取数参数 {key!r}",
            detail={"source": "hithink", "param": key},
        )
    return value


def _require_int(args: dict[str, Any], key: str) -> int:
    """取必填整型参数，缺失即抛 ``UpstreamError``。"""
    value = args.get(key)
    if value is None:
        raise UpstreamError(
            f"hithink 缺少必填取数参数 {key!r}",
            detail={"source": "hithink", "param": key},
        )
    return int(value)


@register_provider
class HithinkProvider(BaseProvider):
    """同花顺 HTTP 数据源（日线 / 涨停池 / 连板天梯 / 交易日历）。"""

    source_id: ClassVar[str] = "hithink"
    label: ClassVar[str] = "同花顺（fuyao Financial API）"
    kind: ClassVar[SourceKind] = SourceKind.HTTP
    capabilities: ClassVar[tuple[str, ...]] = (
        "daily_bars",
        "limit_up_pool",
        "limit_up_pool_supplement",
        "ladder",
        "trading_calendar",
    )
    rate_limit_per_min: ClassVar[int] = 120
    priority: ClassVar[int] = 10

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """初始化。

        Args:
            client: 复用的 HTTP 客户端；``None`` 时每次 fetch 惰性新建（测试注入 MockTransport）。
            base_url: 上游基址覆盖；缺省用 :data:`DEFAULT_BASE_URL`。
            api_key: 显式密钥；缺省读 ``settings.hithink_api_key``。
        """
        self._client = client
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key if api_key is not None else get_settings().hithink_api_key

    # ------------------------------------------------------------ 内部

    def _headers(self) -> dict[str, str]:
        """构造请求头；未配置密钥时立即抛错（不触网）。"""
        if not self._api_key:
            raise UpstreamError(
                "hithink 未配置 API Key（HITHINK_API_KEY）",
                detail={"source": self.source_id, "setting": "hithink_api_key"},
            )
        return {"X-api-key": self._api_key, "User-Agent": "gold-desire/0.1"}

    def _route(self, capability: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """把能力解析为 ``(path, params)``；未知能力或缺少必填参数即抛错。"""
        if capability == "daily_bars":
            return (
                "/api/a-share/prices/historical",
                {
                    "thscode": _require_str(args, "thscode"),
                    "interval": "1d",
                    "start": _require_int(args, "start_ms"),
                    "end": _require_int(args, "end_ms"),
                    "adjust": str(args.get("adjust", "none")),
                },
            )
        if capability in ("limit_up_pool", "limit_up_pool_supplement"):
            # supplement 与主能力同一端点；区别只在主备链（supplement 固定 hithink）
            params: dict[str, Any] = {
                "page": int(args.get("page", 1)),
                "size": int(args.get("size", 200)),
                "sort_field": str(args.get("sort_field", "continue_day_cnt")),
                "sort_dir": str(args.get("sort_dir", "desc")),
            }
            if args.get("date_ms") is not None:
                params["date_ms"] = int(args["date_ms"])
            return "/api/a-share/special-data/limit-up-pool", params
        if capability == "ladder":
            return "/api/a-share/special-data/limit-up-ladder", {}
        if capability == "trading_calendar":
            return "/api/a-share/calendar/trading-days", {}
        raise UpstreamError(
            f"hithink 不支持能力 {capability!r}",
            detail={"source": self.source_id, "capability": capability},
        )

    def _unwrap(self, response: httpx.Response, path: str) -> dict[str, Any]:
        """解析响应体并校验业务码，返回**原始信封**。"""
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError(
                f"hithink {path} 返回非 JSON 响应",
                detail={"source": self.source_id, "path": path, "status": response.status_code},
            ) from exc
        if not isinstance(body, dict):
            raise UpstreamError(
                f"hithink {path} 响应体不是对象",
                detail={"source": self.source_id, "path": path},
            )
        code = body.get("code")
        if code != _SUCCESS_CODE:
            kind = "认证失败" if code in _AUTH_CODES else "业务错误"
            raise UpstreamError(
                f"hithink {path} {kind} code={code} message={body.get('message', '')!s:.120}",
                detail={
                    "source": self.source_id,
                    "path": path,
                    "code": code,
                    "message": body.get("message"),
                },
            )
        return body

    # ------------------------------------------------------------ fetch

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        """取回该能力的**原始**上游 payload（含 ``code`` / ``message`` 信封）。

        Raises:
            UpstreamError: 未配置密钥、缺少必填参数、业务码非 0，或请求失败。
        """
        headers = self._headers()  # 先校验配置，缺失密钥时不触网
        path, params = self._route(capability, args)
        client = self._client
        owns = client is None
        if client is None:
            client = _make_client()
        try:
            response = await request_with_retry(
                client,
                "GET",
                self._base_url + path,
                source=self.source_id,
                params=params,
                headers=headers,
            )
        finally:
            if owns:
                await client.aclose()
        return self._unwrap(response, path)
