"""声明式字段映射 DSL：``FieldMap`` / ``CapabilityMapping`` 与映射执行。

核心思想：**新增一个数据源 = 新增一份映射数据，而非新增代码分支**。
映射用声明式结构描述「源 payload 路径 → 契约字段 + 单位换算」，与 provider
实现解耦，可独立阅读、可放进 YAML 由非 Python 开发者维护。

路径语法（``record_path`` / ``columns_path`` / ``FieldMap.source``）
------------------------------------------------------------------

- 点号分层：``"data.result.symbol"``
- 列表索引：``"data.items[0].symbol"``；**支持负索引**（``"data[-1]"`` 取最后一个元素，
  如选股通当日最后一个分钟情绪点）
- **通配摊平**：``[]`` / ``[*]`` / 单独的 ``*`` 表示「逐项展开」

  - 遇到 list 逐元素展开；遇到 dict 逐 value 展开（保持插入顺序）；
  - 可连续书写，如 ``"data.item[].boards.*[]"`` 表示「逐 item → 逐板块分组 → 逐票」；
  - 写在 ``FieldMap.source`` 中时（如 ``"stocks[].symbol"``）结果为**列表**。

- **祖先作用域**：``FieldMap.source`` 以 ``"^"`` 开头表示从**祖先作用域**取值，
  就近优先（解决「上级标量注入每条记录」）：

  - ``"^thscode"`` 取到 ``data.thscode``，用于 ``data.item[]`` 的每条日线；
  - ``"^date"`` 取到 ``data.item[].date``，用于 ``boards`` 分组内的每条天梯记录。

- **记录序号**：``FieldMap.source == "@index"``，值为记录在结果集中的 0 起序号
  （配合 ``index_to_rank`` 可派生 1 起排名）。
- **运行时上下文**：``FieldMap.context``（如 ``"args.date"``）从调用方传入的
  ``context`` 取值（取数参数、目标交易日等 payload 之外的输入）。

二维数组（``columns_path``）
---------------------------

部分上游把表格编码为「表头数组 + 行数组」（如选股通题材个股的
``data.fields`` + ``data.items``）。此时声明：

- ``record_path = "data.items"``：行数组路径；
- ``columns_path = "data.fields"``：表头路径。

执行时先按表头把每行还原为对象，再走常规字段映射。

一行展开为多行（``explode_path``）
----------------------------------

当一行记录需要按子列表展开为多行时（如题材个股的 ``plates``：一只个股可属多个
题材），声明 ``explode_path = "plates[]"``。展开后的子项成为记录，原行进入
祖先作用域，字段仍可用 ``^code`` 这类写法回取父行值。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from app.datasources.contracts.base import (
    DEGRADED,
    ContractValidationError,
    ValidationIssue,
)
from app.datasources.mappings.transforms import MappingError, resolve_transform

__all__ = [
    "INDEX_SOURCE",
    "REQUIRED",
    "CapabilityMapping",
    "FieldMap",
    "MappingError",
    "apply_mapping",
]

_BRACKET_RE: Final[re.Pattern[str]] = re.compile(r"\[(\*|-\d+|\d*)\]")

#: ``FieldMap.source`` 特殊值：当前记录的 0 起序号。
INDEX_SOURCE: Final[str] = "@index"

#: ``FieldMap.source`` 前缀：从祖先作用域取值（就近优先）。
_ANCESTOR_PREFIX: Final[str] = "^"


class _Each:
    """路径中的「逐项展开」标记，对应 ``[]`` / ``[*]`` / 单独的 ``*``。"""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<EACH>"


_EACH: Final[_Each] = _Each()


class _RequiredSentinel:
    """``REQUIRED`` 哨兵：表示字段缺失即校验失败（无默认值）。"""

    def __repr__(self) -> str:
        return "<REQUIRED>"

    def __bool__(self) -> bool:
        return False


#: 字段无默认值：源缺失时拒绝该记录。
REQUIRED: Final[_RequiredSentinel] = _RequiredSentinel()

#: 内部「路径不存在」哨兵。
_MISSING: Final[Any] = object()


@dataclass(frozen=True)
class FieldMap:
    """单个契约字段的映射声明。

    Attributes:
        target: 契约字段名（领域标准名）。
        source: 源 payload 中的取值路径（支持 ``[]`` 摊平、``^`` 祖先作用域、
            ``@index`` 序号）；``None`` 表示不使用源取值（走 ``context`` /
            ``default``）。
        transform: 转换名（见 :mod:`app.datasources.mappings.transforms`）。
        default: 源缺失时使用的默认值；``REQUIRED`` 表示缺失即报错。
        required: 是否为契约必需字段（与 ``default`` 配合决定缺失行为）。
        context: 运行时上下文取值路径（如 ``"args.date"``）；设置后**优先于**
            ``source``。
    """

    target: str
    source: str | None = None
    transform: str = "identity"
    default: Any = REQUIRED
    required: bool = True
    context: str | None = None


@dataclass(frozen=True)
class CapabilityMapping:
    """一个 (源, 能力) 的完整声明式映射。

    Attributes:
        source_id: 源标识，与 provider 的 ``source_id`` 对应。
        capability: 能力名，须在 ``CAPABILITY_CONTRACTS`` 中。
        record_path: 记录（或记录列表）在 payload 中的路径；``None`` 表示
            payload 本身即记录（dict）或记录列表（list）。
        fields: 字段映射元组。
        static: 每条记录统一注入的常量（如 ``pool_type="limit_up"``）。
        columns_path: 二维数组的表头路径；设置时按表头把每行还原为对象。
        explode_path: 一行展开为多行的子路径（如题材个股的 ``"plates[]"``）；
            展开后的子项成为记录，原行进入祖先作用域（供 ``^字段`` 读取）。
        notes: 人类可读说明，便于后续维护。
    """

    source_id: str
    capability: str
    record_path: str | None
    fields: tuple[FieldMap, ...]
    static: Mapping[str, Any] = field(default_factory=dict)
    columns_path: str | None = None
    explode_path: str | None = None
    notes: str = ""


# ------------------------------------------------------------------ 路径解析


def _split_path(path: str) -> list[str | int | _Each]:
    """把 ``"data.item[].boards.*[]"`` 拆成 ``["data", "item", EACH, "boards", EACH, EACH]``。"""
    tokens: list[str | int | _Each] = []
    for part in path.split("."):
        if not part:
            continue
        last = 0
        for match in _BRACKET_RE.finditer(part):
            _push_key(tokens, part[last : match.start()])
            inner = match.group(1)
            tokens.append(_EACH if inner in ("", "*") else int(inner))
            last = match.end()
        _push_key(tokens, part[last:])
    return tokens


def _push_key(tokens: list[str | int | _Each], key: str) -> None:
    """把路径片段中的「键名」写入 token 列表（``"*"`` 视为摊平标记）。"""
    if not key:
        return
    tokens.append(_EACH if key == "*" else key)


def _is_list(node: Any) -> bool:
    """是否为可索引的序列（排除 str/bytes）。"""
    return isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray))


def _is_blank(value: Any) -> bool:
    """是否为「空值」：``None`` 或去空白后为空串。"""
    return value is None or (isinstance(value, str) and not value.strip())


def _descend(
    node: Any,
    tokens: Sequence[str | int | _Each],
    ancestors: tuple[Mapping[str, Any], ...],
    out: list[tuple[Any, tuple[Mapping[str, Any], ...]]],
) -> None:
    """按 token 递归下钻，收集 ``(记录节点, 祖先作用域)``。

    祖先作用域仅在**按键下钻到 Mapping** 时累积，故 ``^field`` 可就近取到
    上级标量（如 ``data.thscode``、``data.item[].date``）。
    """
    if not tokens:
        out.append((node, ancestors))
        return
    token, rest = tokens[0], tokens[1:]
    if token is _EACH:
        if isinstance(node, Mapping):
            children = list(node.values())
        elif _is_list(node):
            children = list(node)
        else:
            return
        for child in children:
            _descend(child, rest, ancestors, out)
        return
    if isinstance(token, int):
        if not _is_list(node):
            return
        # 支持负索引（``data[-1]`` 取最后一个元素，如当日最后一个分钟点）。
        position = token if token >= 0 else len(node) + token
        if not 0 <= position < len(node):
            return
        _descend(node[position], rest, ancestors, out)
        return
    if not isinstance(node, Mapping) or token not in node:
        return
    _descend(node[token], rest, (*ancestors, node), out)


def _collect(node: Any, tokens: Sequence[str | int | _Each]) -> list[Any]:
    """按 token 收集全部匹配值（含摊平时可能多个）。"""
    out: list[tuple[Any, tuple[Mapping[str, Any], ...]]] = []
    _descend(node, tokens, (), out)
    return [value for value, _ in out]


def _lookup(node: Any, path: str) -> Any:
    """按路径取值。

    含摊平标记时返回**列表**（可能为空）；否则返回单值，路径不存在返回
    ``_MISSING``。
    """
    tokens = _split_path(path)
    values = _collect(node, tokens)
    if any(token is _EACH for token in tokens):
        return values
    return values[0] if values else _MISSING


def _lookup_ancestors(ancestors: Sequence[Mapping[str, Any]], path: str) -> Any:
    """从祖先作用域取值，**就近优先**；全部缺失返回 ``_MISSING``。"""
    for scope in reversed(ancestors):
        value = _lookup(scope, path)
        if value is not _MISSING:
            return value
    return _MISSING


# ------------------------------------------------------------------ 记录抽取


def _issue(
    mapping: CapabilityMapping, field_name: str, reason: str, pointer: str
) -> ValidationIssue:
    """构造一条结构化校验问题。"""
    return ValidationIssue(
        source=mapping.source_id,
        capability=mapping.capability,
        field=field_name,
        reason=reason,
        raw_pointer=pointer,
    )


def _extract_records(
    mapping: CapabilityMapping, payload: Any
) -> list[tuple[Any, tuple[Mapping[str, Any], ...]]]:
    """从 payload 中取出 ``(记录节点, 祖先作用域)`` 列表。"""
    if not mapping.record_path:
        return [(payload, ())]
    tokens = _split_path(mapping.record_path)
    scoped = _collect_scoped(payload, tokens)
    # ``record_path`` 指向数组时（如 "data.item"）隐式摊平一层，
    # 使「路径直达列表」与「路径逐项摊平（"data.item[]"）」写法等价。
    scoped = [
        (element, ancestors)
        for node, ancestors in scoped
        for element in (node if _is_list(node) else [node])
    ]
    if not scoped and not any(token is _EACH for token in tokens):
        raise ContractValidationError(
            [
                _issue(
                    mapping,
                    mapping.record_path,
                    "record_path 在 payload 中不存在",
                    mapping.record_path,
                )
            ]
        )
    return scoped


def _collect_scoped(
    payload: Any, tokens: Sequence[str | int | _Each]
) -> list[tuple[Any, tuple[Mapping[str, Any], ...]]]:
    """按路径收集记录节点及其祖先作用域。"""
    out: list[tuple[Any, tuple[Mapping[str, Any], ...]]] = []
    _descend(payload, tokens, (), out)
    return out


def _apply_columns(
    mapping: CapabilityMapping,
    payload: Any,
    scoped: list[tuple[Any, tuple[Mapping[str, Any], ...]]],
) -> list[tuple[Any, tuple[Mapping[str, Any], ...]]]:
    """二维数组 → 对象列表：按 ``columns_path`` 表头把每行还原为 dict。"""
    if not mapping.columns_path:
        return scoped
    header = _lookup(payload, mapping.columns_path)
    if not _is_list(header):
        raise ContractValidationError(
            [
                _issue(
                    mapping,
                    mapping.columns_path,
                    "columns_path 未解析为列表",
                    mapping.columns_path,
                )
            ]
        )
    columns = [str(name) for name in header]
    rows: list[tuple[Any, tuple[Mapping[str, Any], ...]]] = []
    for node, ancestors in scoped:
        if not _is_list(node):
            raise ContractValidationError(
                [
                    _issue(
                        mapping,
                        mapping.record_path or "<payload>",
                        "二维数组的行不是列表",
                        mapping.record_path or "<payload>",
                    )
                ]
            )
        rows.append((dict(zip(columns, node, strict=False)), ancestors))
    return rows


def _explode(
    mapping: CapabilityMapping,
    scoped: list[tuple[Any, tuple[Mapping[str, Any], ...]]],
) -> list[tuple[Any, tuple[Mapping[str, Any], ...]]]:
    """按 ``explode_path`` 把一条记录展开为多条（如「一行个股 → 其所属多个题材」）。

    展开后的子项成为新记录，原记录进入祖先作用域，故可用 ``^code`` 回取父行字段。
    父行没有可展开子项时该行被丢弃（如个股未归属任何题材）。
    """
    if not mapping.explode_path:
        return scoped
    tokens = _split_path(mapping.explode_path)
    out: list[tuple[Any, tuple[Mapping[str, Any], ...]]] = []
    for node, ancestors in scoped:
        sub: list[tuple[Any, tuple[Mapping[str, Any], ...]]] = []
        _descend(node, tokens, ancestors, sub)
        out.extend(sub)
    return out


# ------------------------------------------------------------------ 字段映射


def _raw_value(
    field_map: FieldMap,
    record: Any,
    index: int,
    ancestors: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any] | None,
) -> Any:
    """解析字段的源取值（含上下文 / 序号 / 祖先作用域等特殊形式）。"""
    if field_map.context:
        return _lookup(context, field_map.context) if context else _MISSING
    source = field_map.source
    if source is None:
        return _MISSING
    if source == INDEX_SOURCE:
        return index
    if source.startswith(_ANCESTOR_PREFIX):
        return _lookup_ancestors(ancestors, source[len(_ANCESTOR_PREFIX) :])
    return _lookup(record, source)


def _map_record(
    mapping: CapabilityMapping,
    record: Any,
    index: int,
    ancestors: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[ValidationIssue]]:
    """映射单条记录，返回 (行, 问题列表)；有问题时行为 None。"""
    issues: list[ValidationIssue] = []
    if not isinstance(record, Mapping):
        issues.append(
            _issue(mapping, "<record>", "记录不是 JSON 对象", f"records[{index}]")
        )
        return None, issues

    row: dict[str, Any] = dict(mapping.static)
    for field_map in mapping.fields:
        pointer = field_map.context or field_map.source or field_map.target
        raw = _raw_value(field_map, record, index, ancestors, context)
        if raw is _MISSING or _is_blank(raw):
            if field_map.default is not REQUIRED:
                row[field_map.target] = field_map.default
                continue
            if not field_map.required:
                row[field_map.target] = None
                continue
            issues.append(
                _issue(
                    mapping,
                    field_map.target,
                    f"必需字段缺失（源路径 {field_map.source!r}）",
                    f"records[{index}].{pointer}",
                )
            )
            continue

        value = resolve_transform(field_map.transform)(raw)
        if value is DEGRADED:
            # 可选字段「算不出就留空」，只有必需字段才算拒绝该记录。
            if field_map.default is not REQUIRED:
                row[field_map.target] = field_map.default
                continue
            if not field_map.required:
                row[field_map.target] = None
                continue
            issues.append(
                _issue(
                    mapping,
                    field_map.target,
                    f"转换 {field_map.transform!r} 无法处理源值（源路径 {field_map.source!r}）",
                    f"records[{index}].{pointer}",
                )
            )
            continue
        row[field_map.target] = value
    return row, issues


def apply_mapping(
    mapping: CapabilityMapping,
    payload: Any,
    *,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """把源 payload 按声明式映射转换为契约字段行（未做 Pydantic 校验）。

    任一条记录存在**必需字段**缺失或转换失败即整体抛
    :class:`ContractValidationError`（**拒绝而非填 0/空串**）。

    标注为可选（``required=False``）或带 ``default`` 的字段遵循「能算的算、
    算不出的留空」：缺失或转换失败时写入 ``None`` / 默认值，不拒绝整批记录。

    Args:
        mapping: 声明式映射。
        payload: provider ``fetch()`` 返回的原始 payload。
        context: 运行时上下文（如 ``{"args": {...}}``），供 ``FieldMap.context`` 取值。

    Raises:
        ContractValidationError: 存在被拒绝的记录。
    """
    scoped = _extract_records(mapping, payload)
    scoped = _apply_columns(mapping, payload, scoped)
    scoped = _explode(mapping, scoped)
    rows: list[dict[str, Any]] = []
    issues: list[ValidationIssue] = []
    for index, (record, ancestors) in enumerate(scoped):
        row, record_issues = _map_record(mapping, record, index, ancestors, context)
        if record_issues:
            issues.extend(record_issues)
            continue
        assert row is not None
        rows.append(row)
    if issues:
        raise ContractValidationError(issues)
    return rows
