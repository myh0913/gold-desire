"""页面 key 注册表：角色权限矩阵的唯一权威来源。

旧项目 ``quant-web/src/pages/设置.tsx`` 的 ``PAGE_MATRIX`` 是**手工维护**的常量数组，
且遗漏了 ``review``（复盘）页，导致 admin 保存某角色权限时该页面被静默丢弃。

本模块以**单一注册表**取代手工矩阵，使该类缺陷在构造上不可能发生：

- :class:`PageKey` 枚举列出全部已知页面 key（覆盖 spec Phase 1 + Phase 2）；
- :func:`all_page_keys` / :func:`page_matrix` **始终从注册表派生**权限矩阵，
  新增页面只需 ``register_page``（或扩展枚举），矩阵自动包含它，无法被静默遗漏；
- :func:`validate_page_keys` 拒绝未知 key，写入侧不可能持久化脏数据；
- 注册表可运行时扩展（:func:`register_page`），供「新增页面后仍正确回读」测试。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from app.core.errors import ValidationError


class PageKey(StrEnum):
    """规范页面 key（Phase 1 + Phase 2 全覆盖）。"""

    OVERVIEW = "overview"
    ADVICE = "advice"
    REVIEW = "review"
    POOLS = "pools"
    LADDER = "ladder"
    NEWSFLASH = "newsflash"
    THEMES = "themes"
    MONITOR = "monitor"
    QUANTCONFIG = "quantconfig"
    SETTINGS = "settings"
    BACKTEST = "backtest"


@dataclass(frozen=True, slots=True)
class PageMeta:
    """页面元数据：key、显示名、是否仅管理员可见。"""

    key: str
    label: str
    admin_only: bool = False


_DEFAULT_PAGES: tuple[PageMeta, ...] = (
    PageMeta(PageKey.OVERVIEW.value, "总览"),
    PageMeta(PageKey.ADVICE.value, "今日建议"),
    PageMeta(PageKey.REVIEW.value, "复盘"),
    PageMeta(PageKey.POOLS.value, "涨停池"),
    PageMeta(PageKey.LADDER.value, "连板天梯"),
    PageMeta(PageKey.NEWSFLASH.value, "7×24 快讯"),
    PageMeta(PageKey.THEMES.value, "主题机会"),
    PageMeta(PageKey.MONITOR.value, "监管名单"),
    PageMeta(PageKey.QUANTCONFIG.value, "量化配置", admin_only=True),
    PageMeta(PageKey.SETTINGS.value, "设置", admin_only=True),
    PageMeta(PageKey.BACKTEST.value, "回测"),
)

_registry: dict[str, PageMeta] = {meta.key: meta for meta in _DEFAULT_PAGES}

ALL_PAGE_KEYS: tuple[str, ...] = tuple(PageKey)
"""全部规范页面 key（枚举快照，顺序稳定）。"""


def all_page_keys() -> tuple[str, ...]:
    """返回当前注册表内的全部页面 key（含运行时新增页面）。"""
    return tuple(_registry)


def page_meta(key: str) -> PageMeta | None:
    """按 key 取页面元数据，不存在返回 ``None``。"""
    return _registry.get(key)


def page_labels() -> dict[str, str]:
    """返回 ``key -> 显示名`` 映射（从注册表派生）。"""
    return {key: meta.label for key, meta in _registry.items()}


def admin_only_pages() -> frozenset[str]:
    """返回仅管理员可见的页面 key 集合。"""
    return frozenset(key for key, meta in _registry.items() if meta.admin_only)


def is_known_page(key: str) -> bool:
    """判断 key 是否已在注册表中登记。"""
    return key in _registry


def register_page(key: str, label: str, *, admin_only: bool = False) -> PageMeta:
    """向注册表登记（或覆盖）一个页面 key。

    作为「新增页面」的扩展点：登记后 :func:`all_page_keys` / :func:`page_matrix`
    自动包含该页面，无需改动任何权限矩阵代码。
    """
    meta = PageMeta(key=str(key), label=label, admin_only=admin_only)
    _registry[meta.key] = meta
    return meta


def reset_registry() -> None:
    """把注册表恢复为内置页面集合（测试辅助）。"""
    _registry.clear()
    _registry.update({meta.key: meta for meta in _DEFAULT_PAGES})


def validate_page_keys(keys: Iterable[str]) -> list[str]:
    """校验页面 key 合法性并返回去重后的注册表顺序列表。

    Raises:
        ValidationError: 存在未登记的 key（拒绝写入，绝不静默丢弃）。
    """
    requested = {str(key) for key in keys}
    unknown = sorted(key for key in requested if key not in _registry)
    if unknown:
        raise ValidationError(
            f"未知页面 key：{', '.join(unknown)}",
            code="unknown_page_key",
            detail={"unknown_pages": unknown},
        )
    return [key for key in all_page_keys() if key in requested]


def effective_pages(stored: Iterable[str] | None, *, is_admin: bool) -> list[str]:
    """从注册表派生角色的**有效**页面集合。

    Args:
        stored: 已持久化的授权页面 key；``None`` 表示从未配置（走默认）。
        is_admin: 是否为管理员角色（管理员恒为全部页面）。

    Returns:
        注册表顺序的有效页面 key 列表。由于始终以注册表为基准，新增页面会自动
        出现在结果中，绝不会因旧矩阵遗漏而被静默丢弃。
    """
    known = all_page_keys()
    if is_admin:
        return list(known)
    if stored is None:
        blocked = admin_only_pages()
        return [key for key in known if key not in blocked]
    granted = {str(key) for key in stored}
    return [key for key in known if key in granted]


def page_matrix(stored: Iterable[str] | None, *, is_admin: bool) -> dict[str, bool]:
    """返回 ``页面 key -> 是否授权`` 的完整矩阵（行集恒等于注册表）。"""
    granted = set(effective_pages(stored, is_admin=is_admin))
    return {key: key in granted for key in all_page_keys()}


__all__ = [
    "ALL_PAGE_KEYS",
    "PageKey",
    "PageMeta",
    "admin_only_pages",
    "all_page_keys",
    "effective_pages",
    "is_known_page",
    "page_labels",
    "page_matrix",
    "page_meta",
    "register_page",
    "reset_registry",
    "validate_page_keys",
]
