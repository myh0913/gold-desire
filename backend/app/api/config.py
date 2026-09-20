"""量化配置路由：策略与因子的定义/版本读接口 + admin 写接口。

读接口需「量化配置」页面权限；写接口 admin-only，且服务层写审计并失效缓存。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_admin, require_page
from app.core.pages import PageKey
from app.models.auth import User
from app.repositories import Repositories, get_repositories
from app.schemas.config import (
    ConfigDiffOut,
    ConfigVersionOut,
    ConfigVersionsResponse,
    FactorConfigUpdate,
    FactorEffectivenessResponse,
    FactorOut,
    FactorsResponse,
    StrategiesResponse,
    StrategyConfigUpdate,
    StrategyOut,
    StrategyStateOut,
)
from app.services.config_service import ConfigService

router = APIRouter(tags=["config"])

_require_quantconfig = require_page(PageKey.QUANTCONFIG)


def get_config_service(repos: Repositories = Depends(get_repositories)) -> ConfigService:
    """请求级配置服务。"""
    return ConfigService(repos)


# ------------------------------------------------------------------ 策略读


@router.get("/strategies", response_model=StrategiesResponse)
async def list_strategies(
    _: User = Depends(_require_quantconfig),
    service: ConfigService = Depends(get_config_service),
) -> StrategiesResponse:
    """列出全部策略（定义 + 参数 schema + 生效参数 + 版本历史）。"""
    return await service.strategies()


@router.get("/strategies/{strategy_id}", response_model=StrategyOut)
async def get_strategy_detail(
    strategy_id: str,
    _: User = Depends(_require_quantconfig),
    service: ConfigService = Depends(get_config_service),
) -> StrategyOut:
    """取单个策略详情。"""
    return await service.strategy(strategy_id)


@router.get("/strategies/{strategy_id}/versions", response_model=ConfigVersionsResponse)
async def list_strategy_versions(
    strategy_id: str,
    limit: int | None = Query(default=None, ge=1),
    _: User = Depends(_require_quantconfig),
    service: ConfigService = Depends(get_config_service),
) -> ConfigVersionsResponse:
    """策略参数版本历史。"""
    return await service.strategy_versions(strategy_id, limit)


@router.get("/strategies/{strategy_id}/versions/diff", response_model=ConfigDiffOut)
async def diff_strategy_versions(
    strategy_id: str,
    from_version: int = Query(alias="from", ge=1),
    to_version: int = Query(alias="to", ge=1),
    _: User = Depends(_require_quantconfig),
    service: ConfigService = Depends(get_config_service),
) -> ConfigDiffOut:
    """比较两个策略参数版本。"""
    return await service.strategy_version_diff(strategy_id, from_version, to_version)


# ------------------------------------------------------------------ 策略写


@router.put("/strategies/{strategy_id}/config", response_model=ConfigVersionOut)
async def save_strategy_config(
    strategy_id: str,
    payload: StrategyConfigUpdate,
    actor: User = Depends(require_admin),
    service: ConfigService = Depends(get_config_service),
) -> ConfigVersionOut:
    """保存并启用策略参数（新建版本并置 active）。"""
    return await service.save_strategy_config(
        strategy_id, payload.params, note=payload.note, actor=actor.username
    )


@router.post("/strategies/{strategy_id}/rollback/{version}", response_model=ConfigVersionOut)
async def rollback_strategy(
    strategy_id: str,
    version: int,
    actor: User = Depends(require_admin),
    service: ConfigService = Depends(get_config_service),
) -> ConfigVersionOut:
    """回滚策略参数到历史版本。"""
    return await service.rollback_strategy(strategy_id, version, actor=actor.username)


@router.post("/strategies/{strategy_id}/enable", response_model=StrategyStateOut)
async def enable_strategy(
    strategy_id: str,
    actor: User = Depends(require_admin),
    service: ConfigService = Depends(get_config_service),
) -> StrategyStateOut:
    """启用策略。"""
    return await service.set_strategy_enabled(strategy_id, True, actor=actor.username)


@router.post("/strategies/{strategy_id}/disable", response_model=StrategyStateOut)
async def disable_strategy(
    strategy_id: str,
    actor: User = Depends(require_admin),
    service: ConfigService = Depends(get_config_service),
) -> StrategyStateOut:
    """停用策略。"""
    return await service.set_strategy_enabled(strategy_id, False, actor=actor.username)


# ------------------------------------------------------------------ 因子读


@router.get("/factors", response_model=FactorsResponse)
async def list_factors(
    _: User = Depends(_require_quantconfig),
    service: ConfigService = Depends(get_config_service),
) -> FactorsResponse:
    """列出全部因子（定义 + 参数 schema + 生效参数）。"""
    return await service.factors()


@router.get("/factors/{factor_id}", response_model=FactorOut)
async def get_factor_detail(
    factor_id: str,
    _: User = Depends(_require_quantconfig),
    service: ConfigService = Depends(get_config_service),
) -> FactorOut:
    """取单个因子详情。"""
    return await service.factor(factor_id)


@router.get("/factors/{factor_id}/effectiveness", response_model=FactorEffectivenessResponse)
async def get_factor_effectiveness(
    factor_id: str,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    min_boards: int = Query(default=2, ge=1, le=10),
    _: User = Depends(_require_quantconfig),
    service: ConfigService = Depends(get_config_service),
) -> FactorEffectivenessResponse:
    """因子有效性统计（按档位 + A/B/C 分段，绝不只报聚合）。"""
    return await service.factor_effectiveness(
        factor_id, start=start, end=end, min_boards=min_boards
    )


@router.get("/factors/{factor_id}/versions", response_model=ConfigVersionsResponse)
async def list_factor_versions(
    factor_id: str,
    limit: int | None = Query(default=None, ge=1),
    _: User = Depends(_require_quantconfig),
    service: ConfigService = Depends(get_config_service),
) -> ConfigVersionsResponse:
    """因子参数版本历史。"""
    return await service.factor_versions(factor_id, limit)


@router.get("/factors/{factor_id}/versions/diff", response_model=ConfigDiffOut)
async def diff_factor_versions(
    factor_id: str,
    from_version: int = Query(alias="from", ge=1),
    to_version: int = Query(alias="to", ge=1),
    _: User = Depends(_require_quantconfig),
    service: ConfigService = Depends(get_config_service),
) -> ConfigDiffOut:
    """比较两个因子参数版本。"""
    return await service.factor_version_diff(factor_id, from_version, to_version)


# ------------------------------------------------------------------ 因子写


@router.put("/factors/{factor_id}/config", response_model=ConfigVersionOut)
async def save_factor_config(
    factor_id: str,
    payload: FactorConfigUpdate,
    actor: User = Depends(require_admin),
    service: ConfigService = Depends(get_config_service),
) -> ConfigVersionOut:
    """保存并启用因子参数（新建版本并置 active）。"""
    return await service.save_factor_config(
        factor_id, payload.params, note=payload.note, actor=actor.username
    )


@router.post("/factors/{factor_id}/rollback/{version}", response_model=ConfigVersionOut)
async def rollback_factor(
    factor_id: str,
    version: int,
    actor: User = Depends(require_admin),
    service: ConfigService = Depends(get_config_service),
) -> ConfigVersionOut:
    """回滚因子参数到历史版本。"""
    return await service.rollback_factor(factor_id, version, actor=actor.username)


__all__ = ["get_config_service", "router"]
