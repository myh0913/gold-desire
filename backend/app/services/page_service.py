"""页面 key 注册表服务：把 :mod:`app.core.pages` 的权威注册表暴露为只读响应。

前端据其派生导航与权限矩阵，故该服务**不涉及权限校验**（任何已登录用户可读），
只从注册表读取，绝不硬编码页面清单。
"""

from __future__ import annotations

from app.core.pages import admin_only_pages, all_page_keys, page_labels
from app.repositories import Repositories
from app.schemas.pages import PageMetaOut, PagesResponse
from app.services.cache_policy import CachePolicy, get_cache_policy, query_key

__all__ = ["PageService"]


class PageService:
    """页面注册表服务。"""

    def __init__(self, repos: Repositories, policy: CachePolicy | None = None) -> None:
        self._repos = repos
        self._policy = policy if policy is not None else get_cache_policy()

    async def pages(self) -> PagesResponse:
        """返回全部已注册页面 key（含运行时新增）、显示名与是否仅管理员。"""
        key = query_key("pages", {"view": "registry"})

        async def loader() -> PagesResponse:
            labels = page_labels()
            blocked = admin_only_pages()
            return PagesResponse(
                items=[
                    PageMetaOut(
                        key=item, label=labels.get(item, item), admin_only=item in blocked
                    )
                    for item in all_page_keys()
                ]
            )

        return await self._policy.get_or_load("pages", key, loader, PagesResponse.model_validate)
