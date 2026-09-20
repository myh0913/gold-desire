"""读路径两级缓存策略：L1 进程内 LRU（短 TTL）+ L2 共享后端（分级 TTL）。

设计（spec「读 API 与高并发」）：

- **TTL 表是数据**：:data:`TTL_TABLE` 按数据域集中声明 L2/L1 TTL，避免散落常量；
- **键含全部查询参数**：由 :func:`query_key` 统一拼装（含日期、过滤、分页），
  不同查询不会互相串台；
- **写后失效 + TTL 兜底**：admin 写接口与采集完成调用 :meth:`CachePolicy.invalidate`；
- **优雅降级**：缓存后端任何异常只记日志并**落回 DB**，绝不使请求失败。

缓存值统一以 Pydantic 模型 ``model_dump(mode="json")`` 的 JSON 形态存储，故
MemoryCache 与 RedisCache 行为一致，Redis 下可跨进程共享。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.cache import CacheBackend, MemoryCache, cache_key, get_cache

__all__ = [
    "DEFAULT_RULE",
    "TTL_TABLE",
    "CachePolicy",
    "TtlRule",
    "get_cache_policy",
    "query_key",
    "reset_cache_policy",
]

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class TtlRule:
    """某个数据域的缓存策略。

    Attributes:
        namespace: 键前缀（同时作为失效前缀）。
        l2_ttl: L2（共享后端）TTL 秒数。
        l1_ttl: L1（进程内）TTL 秒数，应显著短于 ``l2_ttl``。
    """

    namespace: str
    l2_ttl: int
    l1_ttl: int


#: 分级 TTL 表（spec：实时情绪/池 30s、天梯/主题 60s、日线 300s、历史情绪 600s…）。
TTL_TABLE: dict[str, TtlRule] = {
    rule.namespace: rule
    for rule in (
        TtlRule("sentiment_live", 30, 10),  # 实时情绪
        TtlRule("pool", 30, 10),  # 涨停池
        TtlRule("ladder", 60, 15),  # 连板天梯
        TtlRule("theme", 60, 15),  # 主题
        TtlRule("newsflash", 60, 15),  # 快讯
        TtlRule("daily_bars", 300, 15),  # 日线
        TtlRule("minute_bars", 300, 15),  # 分时
        TtlRule("sentiment_history", 600, 15),  # 历史情绪
        TtlRule("stock", 300, 15),  # 股票基础信息
        TtlRule("monitor", 300, 15),  # 监管名单
        TtlRule("report", 60, 15),  # 报告类（建议 / 回测）
        TtlRule("config", 300, 15),  # 配置类（策略 / 因子定义）
        TtlRule("datasource", 60, 15),  # 数据源注册表
        TtlRule("ingest", 30, 10),  # 采集任务与健康度
        TtlRule("pages", 300, 15),  # 页面注册表
    )
}

#: 未登记数据域的回退策略。
DEFAULT_RULE = TtlRule("default", 60, 10)


def query_key(namespace: str, params: Mapping[str, Any] | None = None) -> str:
    """拼装缓存键：``<namespace>:<k=v>...``（键按名排序，跳过 ``None``）。

    参数覆盖日期、过滤条件与分页，确保不同查询键不同。
    """
    segments: list[Any] = [namespace]
    if params:
        for name in sorted(params):
            value = params[name]
            if value is None:
                continue
            segments.append(f"{name}={value}")
    return cache_key(*segments)


def _dump(value: Any) -> Any:
    """把缓存值转为 JSON 友好形态（Pydantic 模型 → JSON dict）。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


class CachePolicy:
    """两级缓存门面（L1 进程内 + L2 共享后端）。

    Args:
        backend: L2 后端；``None`` 时用进程内单例（:func:`app.core.cache.get_cache`）。
        l1: L1 后端；``None`` 时新建独立 :class:`MemoryCache`。
    """

    def __init__(
        self, backend: CacheBackend | None = None, *, l1: MemoryCache | None = None
    ) -> None:
        self._l2: CacheBackend = backend if backend is not None else get_cache()
        self._l1: CacheBackend = (
            l1 if l1 is not None else MemoryCache(max_entries=4096, default_ttl=15)
        )

    def rule(self, namespace: str) -> TtlRule:
        """取某数据域的 TTL 规则（未登记则回退 :data:`DEFAULT_RULE`）。"""
        return TTL_TABLE.get(namespace, DEFAULT_RULE)

    async def _safe_get(self, cache: CacheBackend, key: str) -> Any | None:
        """读缓存；后端异常时记日志并视为未命中（优雅降级）。"""
        try:
            return await cache.get(key)
        except Exception:
            logger.warning("cache_get_failed", exc_info=True, extra={"key": key})
            return None

    async def _safe_set(self, cache: CacheBackend, key: str, value: Any, ttl: int) -> None:
        """写缓存；后端异常时记日志并忽略（绝不使请求失败）。"""
        try:
            await cache.set(key, value, ttl)
        except Exception:
            logger.warning("cache_set_failed", exc_info=True, extra={"key": key})

    async def get_or_load(
        self,
        namespace: str,
        key: str,
        loader: Callable[[], Awaitable[T]],
        restore: Callable[[Any], T],
    ) -> T:
        """按两级缓存取数：命中返回缓存，未命中执行 ``loader`` 并回填。

        Args:
            namespace: 数据域（决定 TTL）。
            key: 完整缓存键（见 :func:`query_key`）。
            loader: 未命中时的加载函数（只读 DB）。
            restore: 把缓存 JSON 还原为响应模型的函数（如 ``Model.model_validate``）。
        """
        rule = self.rule(namespace)
        hit = await self._safe_get(self._l1, key)
        if hit is not None:
            return restore(hit)
        hit = await self._safe_get(self._l2, key)
        if hit is not None:
            await self._safe_set(self._l1, key, hit, rule.l1_ttl)
            return restore(hit)

        value = await loader()
        payload = _dump(value)
        await self._safe_set(self._l2, key, payload, rule.l2_ttl)
        await self._safe_set(self._l1, key, payload, rule.l1_ttl)
        return value

    async def invalidate(self, namespace: str) -> int:
        """按前缀失效（写后失效 + 采集完成）；返回删除条目数。"""
        removed = 0
        try:
            removed += await self._l2.delete_prefix(namespace)
        except Exception:
            logger.warning("cache_invalidate_failed", exc_info=True, extra={"namespace": namespace})
        try:
            removed += await self._l1.delete_prefix(namespace)
        except Exception:
            logger.warning(
                "cache_l1_invalidate_failed", exc_info=True, extra={"namespace": namespace}
            )
        return removed

    async def clear(self) -> None:
        """清空两级缓存（测试辅助）。"""
        for cache in (self._l1, self._l2):
            try:
                await cache.delete_prefix("")
            except Exception:  # pragma: no cover - 测试辅助
                logger.warning("cache_clear_failed", exc_info=True)


_policy: CachePolicy | None = None


def get_cache_policy() -> CachePolicy:
    """返回进程内缓存策略单例。"""
    global _policy
    if _policy is None:
        _policy = CachePolicy()
    return _policy


def reset_cache_policy() -> None:
    """丢弃缓存策略单例（测试隔离用）。"""
    global _policy
    _policy = None
