"""数据源 provider 协议与通用工具：类型枚举、重试、令牌桶限流、元信息。

provider 只负责「按能力取回原始 payload（上游口径）」，字段差异一律交给
``mappings`` 声明式映射处理。业务代码只依赖 :func:`app.datasources.resolve.resolve`，
SHALL NOT 直接 import 任何具体 provider。
"""

from __future__ import annotations

import asyncio
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any, ClassVar, Final

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import UpstreamError

__all__ = [
    "BaseProvider",
    "ProviderMeta",
    "SourceKind",
    "TokenBucket",
    "get_bucket",
    "request_with_retry",
]


class SourceKind(StrEnum):
    """数据源接入类型。"""

    HTTP = "http"
    TCP = "tcp"
    FILE = "file"
    FAKE = "fake"


class ProviderMeta(BaseModel):
    """provider 元信息（供注册表/管理 API/前端展示）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(description="源唯一标识，与 mapping 的 source_id 对应")
    label: str = Field(description="展示名")
    kind: SourceKind = Field(description="接入类型")
    capabilities: tuple[str, ...] = Field(description="声明可提供的能力")
    rate_limit_per_min: int = Field(gt=0, description="该源限频预算（次/分钟）")
    enabled: bool = Field(default=True, description="是否启用")
    priority: int = Field(default=100, description="默认优先级，数值越小越优先")


# ============================================================ 令牌桶限流


class TokenBucket:
    """异步令牌桶：**按源独立**，一个源被限流不会阻塞其他源。

    依赖 asyncio 单线程语义（临界区无 await），因此无需显式锁；``clock`` 与
    ``sleep`` 可注入，便于测试且不产生真实等待。
    """

    def __init__(
        self,
        rate_per_min: int,
        *,
        capacity: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_min <= 0:
            raise ValueError("rate_per_min 必须为正整数")
        self.rate_per_min = rate_per_min
        self._rate_per_sec = rate_per_min / 60.0
        self._capacity = float(capacity if capacity is not None else max(1, rate_per_min))
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self.total_wait_seconds = 0.0

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_sec)
            self._last = now

    def available(self) -> float:
        """当前可用令牌数（按已流逝时间补充后）。"""
        self._refill()
        return self._tokens

    async def acquire(self, tokens: int = 1) -> None:
        """获取令牌；不足时等待相应时长（单次等待，不做忙等）。"""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return
        deficit = tokens - self._tokens
        wait = deficit / self._rate_per_sec
        self.total_wait_seconds += wait
        await self._sleep(wait)
        self._tokens = 0.0
        self._last = self._clock() + wait


_BUCKETS: Final[dict[str, TokenBucket]] = {}


def get_bucket(source_id: str, rate_per_min: int) -> TokenBucket:
    """按 ``source_id`` 取（或惰性创建）令牌桶，实现跨调用、按源独立限流。"""
    bucket = _BUCKETS.get(source_id)
    if bucket is None or bucket.rate_per_min != rate_per_min:
        bucket = TokenBucket(rate_per_min)
        _BUCKETS[source_id] = bucket
    return bucket


def reset_buckets() -> None:
    """清空令牌桶（仅供测试隔离使用）。"""
    _BUCKETS.clear()


# ============================================================ 重试


def _parse_retry_after(value: str | None) -> float | None:
    """解析 ``Retry-After``：支持秒数与 HTTP-date 两种形式。"""
    if not value:
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: float = 30.0,
    max_attempts: int = 3,
    base_backoff: float = 0.5,
    max_backoff: float = 8.0,
    ok_codes: tuple[int, ...] = (200,),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> httpx.Response:
    """带指数退避 + jitter 的异步请求，供 Task 6 的 HTTP provider 复用。

    - 429/503 尊重上游 ``Retry-After``（秒数或 HTTP-date）；
    - 5xx / 429 / 网络异常可重试，4xx（非 429）立即失败；
    - 耗尽尝试次数抛 :class:`UpstreamError`，``detail`` 携带结构化上下文。

    Args:
        client: 复用的 ``httpx.AsyncClient``。
        method: HTTP 方法。
        url: 完整 URL。
        source: 源标识（写入错误上下文）。
        sleep: 等待函数，可注入以避免测试真实等待。
        rng: 随机源，可注入以获得确定性 jitter。
    """
    randomizer = rng or random.Random()
    last_detail: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.request(
                method,
                url,
                params=params,
                headers=headers,
                json=json_body,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            last_detail = {
                "source": source,
                "method": method,
                "url": url,
                "attempts": attempt,
                "reason": f"{type(exc).__name__}: {exc}",
            }
            if attempt >= max_attempts:
                break
            await sleep(_backoff_delay(attempt, base_backoff, max_backoff, randomizer))
            continue

        if response.status_code in ok_codes:
            return response

        retriable = response.status_code >= 500 or response.status_code in (429, 503)
        last_detail = {
            "source": source,
            "method": method,
            "url": url,
            "attempts": attempt,
            "status": response.status_code,
            "reason": f"HTTP {response.status_code}",
        }
        if not retriable or attempt >= max_attempts:
            break
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        delay = (
            retry_after
            if retry_after is not None
            else _backoff_delay(attempt, base_backoff, max_backoff, randomizer)
        )
        await sleep(delay)

    raise UpstreamError(
        f"{source} {method} {url} 请求失败"
        f"（已尝试 {last_detail.get('attempts', max_attempts)} 次）",
        detail=last_detail,
    )


def _backoff_delay(
    attempt: int, base_backoff: float, max_backoff: float, rng: random.Random
) -> float:
    """指数退避 + jitter：``min(max, base * 2**(attempt-1)) * U(0.5, 1.5)``。"""
    raw = min(max_backoff, base_backoff * (2 ** (attempt - 1)))
    return float(raw * rng.uniform(0.5, 1.5))


# ============================================================ provider 协议


class BaseProvider(ABC):
    """数据源 provider 基类：声明元信息 + 实现 ``fetch``。

    子类须定义类属性 ``source_id`` / ``label`` / ``kind`` / ``capabilities`` /
    ``rate_limit_per_min``，并实现 :meth:`fetch` 返回**原始** payload。
    """

    source_id: ClassVar[str] = ""
    label: ClassVar[str] = ""
    kind: ClassVar[SourceKind] = SourceKind.HTTP
    capabilities: ClassVar[tuple[str, ...]] = ()
    rate_limit_per_min: ClassVar[int] = 60
    enabled: ClassVar[bool] = True
    priority: ClassVar[int] = 100

    def supports(self, capability: str) -> bool:
        """本 provider 是否声明支持该能力。"""
        return capability in self.capabilities

    async def acquire(self, tokens: int = 1) -> None:
        """按本源预算获取令牌（按源独立限流）。"""
        bucket = get_bucket(self.source_id, self.rate_limit_per_min)
        await bucket.acquire(tokens)

    @classmethod
    def meta(cls) -> ProviderMeta:
        """构造 provider 元信息。"""
        return ProviderMeta(
            source_id=cls.source_id,
            label=cls.label or cls.source_id,
            kind=cls.kind,
            capabilities=tuple(cls.capabilities),
            rate_limit_per_min=cls.rate_limit_per_min,
            enabled=cls.enabled,
            priority=cls.priority,
        )

    @abstractmethod
    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        """按能力取回原始 payload（上游口径），由 mapping 层负责字段映射。

        Raises:
            UpstreamError: 上游调用失败或返回业务错误。
        """
        raise NotImplementedError
