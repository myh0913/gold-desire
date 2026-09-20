"""策略插件加载器：目录扫描 + 容错导入。

「新增策略 = 新增目录」的落点：本模块扫描 ``app/strategies/plugins/*/`` 下的插件包
（或顶层 ``*.py`` 文件），逐个导入并收集 :class:`~app.strategies.protocol.BaseStrategy`
子类，交由 :func:`app.strategies.registry.register_strategy` 注册。

- **容错**：某个插件导入失败时，**不**让整个应用崩溃——收集为
  :class:`PluginImportError` 并（非 ``strict`` 时）记一条告警日志后继续。
- **重复检测**：``strategy_id`` 冲突由注册表在发现/启动时抛出
  :class:`~app.strategies.protocol.StrategyRegistryError`（信息含两个模块名）。
- ``strict=True`` 时遇导入错误直接抛出（供启动与测试使用）。

> 说明：``examples/`` 目录**不**在本模块的扫描范围内，故示例策略不会被生产发现
> （避免污染 ``strategy_defs`` 与阶段运行）；要上线示例只需把目录移入 ``plugins/``。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from app.strategies.protocol import BaseStrategy, StrategyRegistryError
from app.strategies.registry import register_strategy

__all__ = [
    "DEFAULT_PLUGINS_DIR",
    "PLUGINS_PACKAGE",
    "DiscoveryResult",
    "PluginImportError",
    "discover_plugins",
]

logger = logging.getLogger(__name__)

#: 生成插件模块名所用的包前缀。
PLUGINS_PACKAGE = "app.strategies.plugins"

#: 默认插件目录（生产发现只扫描此处）。
DEFAULT_PLUGINS_DIR: Path = Path(__file__).resolve().parent / "plugins"


@dataclass(frozen=True, slots=True)
class PluginImportError:
    """单个插件导入失败的明细。

    Attributes:
        module: 期望的模块名。
        path: 插件文件路径。
        error: 错误摘要（``"类型: 信息"``）。
    """

    module: str
    path: str
    error: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """一次插件发现的结果。

    Attributes:
        modules: 成功导入的模块名。
        strategies: 本次成功注册的策略类。
        errors: 导入失败的插件明细（``strict=False`` 时非致命）。
    """

    modules: tuple[str, ...] = ()
    strategies: tuple[type[BaseStrategy], ...] = ()
    errors: tuple[PluginImportError, ...] = ()


def _entry_spec(entry: Path) -> tuple[bool, Path, str] | None:
    """判定目录项是否为插件：返回 ``(是否包, 入口文件, 子模块名)``，非插件返回 ``None``。"""
    if entry.is_dir():
        init = entry / "__init__.py"
        if init.is_file():
            return True, init, entry.name
        return None
    if entry.suffix == ".py" and entry.name != "__init__.py":
        return False, entry, entry.stem
    return None


def _load_module(name: str, path: Path, *, is_package: bool, reuse: bool) -> ModuleType:
    """按文件位置导入模块；``reuse=True`` 时复用 ``sys.modules`` 中已有实例。

    ``reuse=False``（自定义目录，测试用）会先移除同名缓存，确保读到最新文件内容。
    """
    if reuse:
        existing = sys.modules.get(name)
        if existing is not None:
            return existing
    else:
        sys.modules.pop(name, None)

    if is_package:
        spec = importlib.util.spec_from_file_location(
            name, path, submodule_search_locations=[str(path.parent)]
        )
    else:
        spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - 防御性分支
        raise ImportError(f"无法为插件 {path} 构造模块 spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _iter_strategy_classes(module: ModuleType) -> list[type[BaseStrategy]]:
    """收集模块命名空间中声明了非空 ``strategy_id`` 的策略子类（按定义顺序去重）。"""
    found: list[type[BaseStrategy]] = []
    seen: set[type[BaseStrategy]] = set()
    for value in vars(module).values():
        if not isinstance(value, type) or value is BaseStrategy:
            continue
        if not issubclass(value, BaseStrategy):
            continue
        if not value.strategy_id or value in seen:
            continue
        seen.add(value)
        found.append(value)
    return found


def discover_plugins(
    *,
    directory: Path | str | None = None,
    package: str | None = None,
    strict: bool = False,
) -> DiscoveryResult:
    """扫描插件目录、导入模块并注册其中的策略。

    Args:
        directory: 插件目录；``None`` 用 :data:`DEFAULT_PLUGINS_DIR`。
        package: 生成模块名所用的包前缀；``None`` 用 :data:`PLUGINS_PACKAGE`。
        strict: 导入失败时是否抛出；``False`` 时收集错误并继续。

    Returns:
        :class:`DiscoveryResult`（成功模块/策略 + 失败明细）。

    Raises:
        StrategyRegistryError: 出现重复 ``strategy_id``（始终抛出，与 ``strict`` 无关）。
    """
    target = Path(directory) if directory is not None else DEFAULT_PLUGINS_DIR
    if not target.is_dir():
        return DiscoveryResult()

    prefix = package if package is not None else PLUGINS_PACKAGE
    reuse = directory is None
    modules: list[str] = []
    strategies: list[type[BaseStrategy]] = []
    errors: list[PluginImportError] = []

    for entry in sorted(target.iterdir()):
        spec_info = _entry_spec(entry)
        if spec_info is None:
            continue
        is_package, path, sub = spec_info
        name = f"{prefix}.{sub}"
        try:
            module = _load_module(name, path, is_package=is_package, reuse=reuse)
        except StrategyRegistryError:
            raise
        except Exception as exc:
            detail = PluginImportError(
                module=name, path=str(path), error=f"{type(exc).__name__}: {exc}"
            )
            errors.append(detail)
            logger.warning(
                "strategy_plugin_import_failed",
                extra={"plugin_module": name, "path": str(path), "error": detail.error},
            )
            if strict:
                raise
            continue

        modules.append(name)
        for cls in _iter_strategy_classes(module):
            register_strategy(cls)
            strategies.append(cls)

    return DiscoveryResult(
        modules=tuple(modules), strategies=tuple(strategies), errors=tuple(errors)
    )
