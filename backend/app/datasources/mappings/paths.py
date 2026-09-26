"""DSL 路径解析（:mod:`app.datasources.mappings.dsl` 的内部支撑）。

把 ``"data.item[].boards.*[]"`` 这类路径表达式拆成 token 序列并在 payload
上按下钻收集匹配值；含摊平标记（``[]`` / ``[*]`` / ``*``）时逐项展开，
祖先作用域在按键下钻到 Mapping 时累积（供 ``^字段`` 就近回取上级标量）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

_BRACKET_RE: Final[re.Pattern[str]] = re.compile(r"\[(\*|-\d+|\d*)\]")

#: ``FieldMap.source`` 前缀：从祖先作用域取值（就近优先）。
_ANCESTOR_PREFIX: Final[str] = "^"


class _Each:
    """路径中的「逐项展开」标记，对应 ``[]`` / ``[*]`` / 单独的 ``*``。"""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<EACH>"


_EACH: Final[_Each] = _Each()

#: 内部「路径不存在」哨兵。
_MISSING: Final[Any] = object()


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
