"""策略依赖注入上下文：策略所需依赖的统一装配与只读门面。

策略通过 :class:`StrategyContext` 获取一切外部依赖（配置、仓储句柄、因子门面、
只读数据门面、时钟、日志、缓存），**SHALL NOT** 直接 import 仓储、数据源或全局单例
（由架构测试 ``tests/test_strategies.py`` 守护）。

- :class:`FactorFacade` / :class:`DataFacade`：策略可见的**只读**能力边界。
  :class:`DefaultFactorFacade` 为因子门面的默认实现（包装因子注册表与结果缓存）；
  数据门面由引擎（Task 12）注入具体实现。
- :class:`StrategyContextFactory`：按 ``(trade_date, repos, settings)`` 构造工厂，
  引擎对每个策略调用 :meth:`StrategyContextFactory.create` 得到**参数已解析**的上下文。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol, cast, runtime_checkable

from app.core.cache import CacheBackend, get_cache
from app.core.config import Settings
from app.factors import FactorResult
from app.factors.base import FactorContext
from app.factors.cache import FactorResultCache, get_or_compute
from app.factors.registry import FactorRepos
from app.factors.registry import resolve_params as resolve_factor_params
from app.repositories import Repositories
from app.strategies.protocol import StrategyRepos, resolve_params

__all__ = [
    "DataFacade",
    "DefaultFactorFacade",
    "FactorFacade",
    "StrategyContext",
    "StrategyContextFactory",
]


def _utcnow() -> datetime:
    """默认时钟：当前 UTC 时间（可注入以做确定性测试）。"""
    return datetime.now(UTC)


def _default_logger() -> logging.Logger:
    """默认日志器（策略名未定时使用包级 logger）。"""
    return logging.getLogger("app.strategies")


@runtime_checkable
class FactorFacade(Protocol):
    """策略可用的因子门面（只读）。"""

    async def compute(
        self,
        factor_id: str,
        ctx: FactorContext,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> FactorResult:
        """计算因子结果（按已解析参数；``params`` 可覆盖单项）。"""
        ...

    async def params(self, factor_id: str) -> dict[str, Any]:
        """取因子的生效参数（覆盖 > active 配置 > 代码默认）。"""
        ...


@runtime_checkable
class DataFacade(Protocol):
    """策略可用的只读数据门面（由引擎注入；策略禁止直连仓储/数据源）。"""

    async def daily_bars(self, code: str, start: date, end: date) -> Sequence[Any]:
        """取 ``[start, end]`` 区间日线。"""
        ...

    async def limit_up_pool(self, trade_date: date) -> Sequence[Any]:
        """取某交易日涨停池。"""
        ...

    async def minute_bars(self, code: str, trade_date: date) -> Sequence[Any]:
        """取某交易日分时序列。"""
        ...


class DefaultFactorFacade:
    """因子门面的默认实现：包装因子注册表与结果缓存（策略只读）。"""

    def __init__(
        self,
        repos: Repositories | None = None,
        cache: FactorResultCache | None = None,
    ) -> None:
        self._repos = repos
        self._cache = cache

    async def params(self, factor_id: str) -> dict[str, Any]:
        """取因子的生效参数。"""
        resolved = await resolve_factor_params(
            factor_id, cast("FactorRepos | None", self._repos)
        )
        return resolved.params

    async def compute(
        self,
        factor_id: str,
        ctx: FactorContext,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> FactorResult:
        """计算因子结果；缓存键含参数版本，版本变化即失效。"""
        resolved = await resolve_factor_params(
            factor_id, cast("FactorRepos | None", self._repos)
        )
        effective = dict(resolved.params)
        if params:
            effective.update(params)
        return await get_or_compute(
            factor_id,
            ctx,
            effective,
            params_version=resolved.version,
            cache=self._cache,
        )


@dataclass(slots=True)
class StrategyContext:
    """策略运行上下文：策略所需依赖的**唯一**入口。

    Attributes:
        strategy_id: 当前策略标识。
        trade_date: 判定基准交易日。
        params: 已解析的生效参数（覆盖 > active 配置 > 代码默认）。
        repos: 仓储容器（框架注入；策略不应直接使用，改用 ``factors`` / ``data`` 门面）。
        factors: 因子门面（只读）。
        data: 只读数据门面（由引擎注入）。
        clock: 可注入的「当前时间」，便于确定性测试。
        logger: 带策略名的结构化日志器。
        cache: 缓存后端。
        settings: 应用配置。
        replay_date: 回放模式的目标日期；``None`` 表示非回放。
        param_version: 生效参数版本号（``v3`` → ``3``）；代码默认时为 ``None``。
    """

    strategy_id: str
    trade_date: date
    params: Mapping[str, Any] = field(default_factory=dict)
    repos: Repositories | None = None
    factors: FactorFacade | None = None
    data: DataFacade | None = None
    clock: Callable[[], datetime] = _utcnow
    logger: logging.Logger = field(default_factory=_default_logger)
    cache: CacheBackend | None = None
    settings: Settings | None = None
    replay_date: date | None = None
    param_version: int | None = None


@dataclass(slots=True)
class StrategyContextFactory:
    """策略上下文工厂：引擎按统一的依赖装配方式构造每个策略的上下文。

    Args:
        repos: 仓储容器。
        settings: 应用配置。
        trade_date: 本次运行的判定基准交易日。
        factors: 因子门面；``None`` 时按 ``repos`` 构造 :class:`DefaultFactorFacade`。
        data: 只读数据门面（由引擎注入）。
        clock: 可注入时钟。
        cache: 缓存后端；``None`` 时使用进程内单例。
        replay_date: 回放模式目标日期。
    """

    repos: Repositories
    settings: Settings
    trade_date: date
    factors: FactorFacade | None = None
    data: DataFacade | None = None
    clock: Callable[[], datetime] = _utcnow
    cache: CacheBackend | None = None
    replay_date: date | None = None

    async def create(self, strategy_id: str) -> StrategyContext:
        """按 ``strategy_id`` 构造参数已解析的策略上下文。

        Raises:
            StrategyRegistryError: 策略未注册。
        """
        from app.strategies.registry import get_strategy

        cls = get_strategy(strategy_id)
        resolved = await resolve_params(cls, cast("StrategyRepos", self.repos))
        return StrategyContext(
            strategy_id=strategy_id,
            trade_date=self.trade_date,
            params=resolved.params,
            repos=self.repos,
            factors=self.factors if self.factors is not None else DefaultFactorFacade(self.repos),
            data=self.data,
            clock=self.clock,
            logger=logging.getLogger(f"app.strategies.{strategy_id}"),
            cache=self.cache if self.cache is not None else get_cache(),
            settings=self.settings,
            replay_date=self.replay_date,
            param_version=resolved.version_no,
        )
