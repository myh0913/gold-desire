"""页面 key 注册表响应契约。

前端据 :class:`PagesResponse` 派生导航与权限矩阵，故该接口对**任意已登录用户**
开放（不要求页面权限）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["PageMetaOut", "PagesResponse"]


class PageMetaOut(BaseModel):
    """单个页面元数据。"""

    key: str = Field(description="页面 key（权限矩阵的行键）")
    label: str = Field(description="显示名")
    admin_only: bool = Field(default=False, description="是否仅管理员可见")


class PagesResponse(BaseModel):
    """全部已注册页面 key（含运行时新增）。"""

    items: list[PageMetaOut] = Field(default_factory=list)
