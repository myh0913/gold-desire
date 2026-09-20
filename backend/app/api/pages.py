"""页面 key 注册表路由：任意已登录用户可读（前端据此派生导航与权限矩阵）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.auth import User
from app.repositories import Repositories, get_repositories
from app.schemas.pages import PagesResponse
from app.services.page_service import PageService

router = APIRouter(tags=["pages"])


def get_page_service(repos: Repositories = Depends(get_repositories)) -> PageService:
    """请求级页面服务。"""
    return PageService(repos)


@router.get("/pages", response_model=PagesResponse)
async def list_pages(
    _: User = Depends(get_current_user),
    service: PageService = Depends(get_page_service),
) -> PagesResponse:
    """返回全部已注册页面 key（含运行时新增）、显示名与是否仅管理员。"""
    return await service.pages()


__all__ = ["get_page_service", "router"]
