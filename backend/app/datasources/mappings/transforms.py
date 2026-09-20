"""声明式映射的转换函数注册表（单位换算 / 类型转换 / 时间归一）。

转换函数签名统一为 ``Callable[[Any], Any]``：

- 成功时返回契约字段所需的值；
- 输入缺失或非法时返回 :data:`DEGRADED`（**绝不返回 0 / ""**），由映射层
  据此拒绝该记录并记录结构化告警。

带参数的转换以 ``"name:arg"`` 形式书写（如 ``"constant:limit_up"``）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.datasources.contracts.base import DEGRADED

__all__ = [
    "PARAM_TRANSFORMS",
    "TRANSFORMS",
    "MappingError",
    "TransformFn",
    "resolve_transform",
]

TransformFn = Callable[[Any], Any]
ParamTransformFactory = Callable[[str], TransformFn]

_SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
_DATE_FORMATS: Final[tuple[str, ...]] = ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d")
_SUFFIXES: Final[tuple[str, ...]] = (".SS", ".SH", ".SZ", ".BJ")
_TIME_RE: Final[re.Pattern[str]] = re.compile(r"(\d{1,2}):(\d{2})")
_HHMMSS_RE: Final[re.Pattern[str]] = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")
#: 「N板」中的连板数（如 "4连板"、"2天2板"）。
_BOARD_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s*[连]?\s*板")
#: "首板" 等价于 1 连板。
_FIRST_BOARD_RE: Final[re.Pattern[str]] = re.compile(r"首板")


class MappingError(ValueError):
    """映射定义/转换解析错误（配置问题，非数据问题）。"""


def _safe(fn: TransformFn) -> TransformFn:
    """包裹转换函数：非法输入返回 ``DEGRADED`` 而非抛错或填默认值。"""

    def wrapper(value: Any) -> Any:
        try:
            return fn(value)
        except (TypeError, ValueError, KeyError, IndexError, AttributeError):
            return DEGRADED

    return wrapper


def _missing(value: Any) -> bool:
    """判断值是否「不存在」：None 或去空白后为空串。"""
    return value is None or (isinstance(value, str) and not value.strip())


# ------------------------------------------------------------------ 基础转换


@_safe
def _identity(value: Any) -> Any:
    return value


@_safe
def _to_str(value: Any) -> Any:
    return DEGRADED if _missing(value) else str(value).strip()


@_safe
def _to_int(value: Any) -> Any:
    if _missing(value):
        return DEGRADED
    return int(float(value))


@_safe
def _to_float(value: Any) -> Any:
    if _missing(value):
        return DEGRADED
    return float(value)


@_safe
def _to_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if _missing(value):
        return DEGRADED
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "open", "开市"}:
        return True
    if text in {"0", "false", "f", "no", "n", "closed", "休市"}:
        return False
    return DEGRADED


# ------------------------------------------------------------------ 单位换算


@_safe
def _pct_to_ratio(value: Any) -> Any:
    """百分数 → 小数：12.5 → 0.125。"""
    if _missing(value):
        return DEGRADED
    return float(value) / 100.0


@_safe
def _ratio_passthrough(value: Any) -> Any:
    """源已是小数口径：原样透传为 float。"""
    if _missing(value):
        return DEGRADED
    return float(value)


@_safe
def _lots_to_shares(value: Any) -> Any:
    """手 → 股：1 手 = 100 股。"""
    if _missing(value):
        return DEGRADED
    return round(float(value) * 100)


@_safe
def _yuan_from_wan(value: Any) -> Any:
    """万元 → 元：×10000。"""
    if _missing(value):
        return DEGRADED
    return float(value) * 10_000.0


@_safe
def _yuan_from_yi(value: Any) -> Any:
    """亿元 → 元：×1e8。"""
    if _missing(value):
        return DEGRADED
    return float(value) * 100_000_000.0


# ------------------------------------------------------------------ 时间归一


@_safe
def _ms_to_datetime(value: Any) -> Any:
    """毫秒时间戳 → 上海时区 aware datetime。"""
    if _missing(value):
        return DEGRADED
    return datetime.fromtimestamp(int(value) / 1000.0, tz=_SHANGHAI)


@_safe
def _ms_to_date(value: Any) -> Any:
    """毫秒时间戳 → 上海时区日期。"""
    if _missing(value):
        return DEGRADED
    return datetime.fromtimestamp(int(value) / 1000.0, tz=_SHANGHAI).date()


@_safe
def _sec_to_datetime(value: Any) -> Any:
    """秒级时间戳 → 上海时区 aware datetime（选股通用秒，hithink 用毫秒）。"""
    if _missing(value):
        return DEGRADED
    return datetime.fromtimestamp(int(value), tz=_SHANGHAI)


@_safe
def _sec_to_date(value: Any) -> Any:
    """秒级时间戳 → 上海时区日期。"""
    if _missing(value):
        return DEGRADED
    return datetime.fromtimestamp(int(value), tz=_SHANGHAI).date()


@_safe
def _str_to_date(value: Any) -> Any:
    """字符串日期 → date；兼容 %Y-%m-%d / %Y%m%d / %Y-%m-%d %H:%M:%S / %Y/%m/%d。"""
    if _missing(value):
        return DEGRADED
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return DEGRADED


@_safe
def _hhmm_from_ms(value: Any) -> Any:
    """毫秒时间戳 → "HH:MM"（上海时区）。"""
    if _missing(value):
        return DEGRADED
    moment = datetime.fromtimestamp(int(value) / 1000.0, tz=_SHANGHAI)
    return moment.strftime("%H:%M")


@_safe
def _hhmm_from_sec(value: Any) -> Any:
    """秒级时间戳 → "HH:MM"（上海时区）。"""
    if _missing(value):
        return DEGRADED
    moment = datetime.fromtimestamp(int(value), tz=_SHANGHAI)
    return moment.strftime("%H:%M")


@_safe
def _hhmm_from_str(value: Any) -> Any:
    """字符串时间 → "HH:MM"；兼容 "09:30" / "09:30:00" / "2026-09-18 09:30:00"。"""
    if _missing(value):
        return DEGRADED
    text = str(value).strip()
    match = _HHMMSS_RE.search(text) or _TIME_RE.search(text)
    if match is None:
        return DEGRADED
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return DEGRADED
    return f"{hour:02d}:{minute:02d}"


# ------------------------------------------------------------------ 派生字段


@_safe
def _rank_from_index(value: Any) -> Any:
    """0 起数组序号 → 1 起排名（上游「数组顺序即排名」时使用）。"""
    return int(value) + 1


@_safe
def _max_key_int(value: Any) -> Any:
    """字典的最大数值键：``{"1":2,"2":3,"4":1}`` → ``4``。

    用于把选股通 ``lianbangaodu``（``{连板数: 家数}``）压成「最高连板高度」。
    """
    if not isinstance(value, Mapping):
        return DEGRADED
    keys: list[int] = []
    for key in value:
        try:
            keys.append(int(str(key).strip()))
        except (TypeError, ValueError):
            continue
    return max(keys) if keys else DEGRADED


@_safe
def _boards_from_text(value: Any) -> Any:
    """连板文本 → 连板数：``"4连板"`` → 4、``"3天2板"`` → 2、``"首板"`` → 1。"""
    if _missing(value):
        return DEGRADED
    text = str(value)
    match = _BOARD_RE.search(text)
    if match is not None:
        return int(match.group(1))
    return 1 if _FIRST_BOARD_RE.search(text) else DEGRADED


# ------------------------------------------------------------------ 代码归一


def _digits(code: str) -> str:
    """剥离交易所后缀/前缀，返回 6 位数字代码；非法返回空串。"""
    text = code.strip().upper()
    for suffix in _SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    if text.startswith(("SH", "SZ", "BJ")):
        text = text[2:]
    return text if len(text) == 6 and text.isdigit() else ""


@_safe
def _strip_suffix(value: Any) -> Any:
    """剥离 .SS/.SZ/.SH/.BJ 后缀，保留 6 位纯数字代码。"""
    if _missing(value):
        return DEGRADED
    digits = _digits(str(value))
    return digits or DEGRADED


@_safe
def _normalize_code(value: Any) -> Any:
    """归一为项目标准代码：6 位数字 + ``.SH`` / ``.SZ`` / ``.BJ``。

    规则与 ``quant-system.pool.normalize_thscode`` 一致（选股通 ``.SS`` 归一为
    ``.SH``）：6 开头 → .SH；0/3 开头 → .SZ；4/8/9 开头 → .BJ；其余非法。
    """
    if _missing(value):
        return DEGRADED
    digits = _digits(str(value))
    if not digits:
        return DEGRADED
    if digits.startswith("6"):
        return f"{digits}.SH"
    if digits.startswith(("0", "3")):
        return f"{digits}.SZ"
    if digits.startswith(("4", "8", "9")):
        return f"{digits}.BJ"
    return DEGRADED


# ------------------------------------------------------------------ 列表


@_safe
def _list_of_str(value: Any) -> Any:
    """列表/逗号分隔字符串 → ``list[str]``。"""
    if value is None:
        return DEGRADED
    if isinstance(value, str):
        items: Iterable[Any] = list(value.split(","))
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        return DEGRADED
    out = [str(item).strip() for item in items if str(item).strip()]
    return out


def _constant_factory(raw: str) -> TransformFn:
    """构造常量转换：忽略源取值，恒返回字面量 ``raw``。"""

    def _constant(_value: Any) -> Any:
        return raw

    return _constant


#: 无参转换注册表。
TRANSFORMS: Final[dict[str, TransformFn]] = {
    "identity": _identity,
    "to_str": _to_str,
    "to_int": _to_int,
    "to_float": _to_float,
    "to_bool": _to_bool,
    "pct_to_ratio": _pct_to_ratio,
    "ratio_passthrough": _ratio_passthrough,
    "lots_to_shares": _lots_to_shares,
    "yuan_from_wan": _yuan_from_wan,
    "yuan_from_yi": _yuan_from_yi,
    "ms_to_datetime": _ms_to_datetime,
    "ms_to_date": _ms_to_date,
    "sec_to_datetime": _sec_to_datetime,
    "sec_to_date": _sec_to_date,
    "str_to_date": _str_to_date,
    "hhmm_from_ms": _hhmm_from_ms,
    "hhmm_from_sec": _hhmm_from_sec,
    "hhmm_from_str": _hhmm_from_str,
    "rank_from_index": _rank_from_index,
    "max_key_int": _max_key_int,
    "boards_from_text": _boards_from_text,
    "strip_suffix": _strip_suffix,
    "normalize_code": _normalize_code,
    "list_of_str": _list_of_str,
}

#: 带参转换工厂注册表（书写形式 ``"name:arg"``）。
PARAM_TRANSFORMS: Final[dict[str, ParamTransformFactory]] = {
    "constant": _constant_factory,
}


def resolve_transform(spec: str) -> TransformFn:
    """把映射中的 ``transform`` 字符串解析为可调用转换函数。

    Raises:
        MappingError: 转换名未注册，或带参转换缺少参数。
    """
    name, _, arg = spec.partition(":")
    factory = PARAM_TRANSFORMS.get(name)
    if factory is not None:
        if not arg:
            raise MappingError(f"带参转换 {name!r} 缺少参数（应写作 '{name}:value'）")
        return factory(arg)
    fn = TRANSFORMS.get(name)
    if fn is None:
        raise MappingError(f"未注册的转换名: {name!r}")
    return fn
