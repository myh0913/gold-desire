"""因子结果缓存：按 ``factor_id + 参数版本 + 交易日 + 代码`` 缓存计算结果。

- 缓存键包含**参数版本**：参数版本变化（admin 调阈值并启用新版本）后键随之改变，
  旧结果自然失效，无需显式清理；
- 结果以 :meth:`FactorResult.to_dict` 序列化后写入后端，兼容内存与 Redis 两种实现；
- :class:`FactorResultCache` 可注入 :class:`~app.core.cache.CacheBackend`，便于测试隔离。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.core.cache import CacheBackend, cache_key, get_cache
from app.factors.base import FactorContext, FactorResult, get_factor

__all__ = [
    "ComputeFn",
    "FactorResultCache",
    "factor_cache_key",
    "get_factor_cache",
    "get_or_compute",
]

#: 计算函数签名，与 :meth:`app.factors.base.BaseFactor.compute` 一致。
ComputeFn = Callable[[FactorContext, Mapping[str, Any]], FactorResult]


def factor_cache_key(
    factor_id: str,
    params_version: str | int,
    trade_date: Any,
    code: str,
) -> str:
    """拼装缓存键：``factor:<id>:<版本>:<交易日>:<代码>``。

    ``params_version`` 直接取自 :attr:`ResolvedParams.version`（如 ``"v3"`` /
    ``"default"``），故版本变化即键变化。
    """
    return cache_key("factor", factor_id, str(params_version), trade_date, code)


class FactorResultCache:
    """因子结果缓存门面。

    Args:
        backend: 缓存后端；``None`` 时使用进程内单例（:func:`app.core.cache.get_cache`）。
        ttl: 条目有效期（秒）；``None`` 表示使用后端默认 TTL。
    """

    def __init__(self, backend: CacheBackend | None = None, *, ttl: int | None = None) -> None:
        self._backend = backend if backend is not None else get_cache()
        self._ttl = ttl

    async def get_or_compute(
        self,
        factor_id: str,
        ctx: FactorContext,
        params: Mapping[str, Any],
        *,
        params_version: str | int,
        compute: ComputeFn | None = None,
    ) -> FactorResult:
        """命中则返回缓存结果，否则计算并写回。

        Args:
            factor_id: 因子标识。
            ctx: 计算上下文（提供交易日与代码）。
            params: 已解析参数。
            params_version: 参数版本（来自 :class:`ResolvedParams.version`）；
                **版本变化即失效**。
            compute: 自定义计算函数；``None`` 时使用注册因子的 ``compute``。

        Raises:
            FactorRegistryError: 因子未注册且未提供 ``compute``。
        """
        key = factor_cache_key(factor_id, params_version, ctx.trade_date, ctx.code)
        cached = await self._backend.get(key)
        if cached is not None:
            return FactorResult.from_dict(cached)

        fn: ComputeFn = compute if compute is not None else get_factor(factor_id)().compute
        result = fn(ctx, params)
        await self._backend.set(key, result.to_dict(), self._ttl)
        return result

    async def invalidate(self, factor_id: str, params_version: str | int | None = None) -> int:
        """按前缀删除缓存；指定 ``params_version`` 时仅删该版本。

        Returns:
            删除条目数。
        """
        prefix = cache_key("factor", factor_id)
        if params_version is not None:
            prefix = cache_key(prefix, str(params_version))
        return await self._backend.delete_prefix(prefix)


_default_cache: FactorResultCache | None = None


def get_factor_cache() -> FactorResultCache:
    """返回进程内单例缓存门面。"""
    global _default_cache
    if _default_cache is None:
        _default_cache = FactorResultCache()
    return _default_cache


async def get_or_compute(
    factor_id: str,
    ctx: FactorContext,
    params: Mapping[str, Any],
    *,
    params_version: str | int,
    compute: ComputeFn | None = None,
    cache: FactorResultCache | None = None,
) -> FactorResult:
    """便捷入口：使用给定（或默认）缓存执行 :meth:`FactorResultCache.get_or_compute`。"""
    store = cache if cache is not None else get_factor_cache()
    return await store.get_or_compute(
        factor_id, ctx, params, params_version=params_version, compute=compute
    )
