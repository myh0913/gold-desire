"""分页/限量工具：保证任何列表接口都**有界**。

- :func:`clamp_limit`：把非分页列表接口的 ``limit``/``days`` 收敛到
  ``[1, settings.page_size_max]``；
- :func:`page_payload`：把仓储 :class:`~app.repositories.base.PageResult` 转为响应
  模型所需的字段字典（``page_size`` 回显**实际生效**值）。
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.repositories.base import PageResult

__all__ = ["clamp_limit", "page_fields"]


def clamp_limit(limit: int | None, *, default: int) -> int:
    """把限量参数收敛到 ``[1, page_size_max]``；``None`` 用 ``default``。"""
    settings = get_settings()
    max_size = max(1, settings.page_size_max)
    resolved = default if limit is None else limit
    return min(max(1, resolved), max_size)


def page_fields(result: PageResult[Any]) -> dict[str, int]:
    """返回分页响应的公共字段（``page_size`` 为实际生效值）。"""
    return {
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "pages": result.pages,
    }
