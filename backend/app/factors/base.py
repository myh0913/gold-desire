"""因子协议层：参数 schema、档位定义、计算结果、计算上下文与注册表。

设计要点（对应 spec「因子注册表与配置中心」）：

- 因子是**纯函数**：只依赖 :class:`FactorContext`（已装配好的行情与派生指标），
  SHALL NOT 直接 import 仓储或数据源。
- 阈值一律通过 :class:`FactorParamSpec` 声明为**可配置参数**，计算与档位划分都从
  参数读取，SHALL NOT 在因子代码中硬编码常量。
- 档位以声明式 :class:`Bucket` 表达，同一份定义既用于硬门槛判定，也用于有效性统计。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar, Literal, TypeVar

__all__ = [
    "DEGRADED_BUCKET",
    "UNMATCHED_BUCKET",
    "Bar",
    "BaseFactor",
    "Bucket",
    "FactorContext",
    "FactorParamSpec",
    "FactorRegistryError",
    "FactorResult",
    "MinutePoint",
    "ParamType",
    "all_factors",
    "format_count",
    "format_percent",
    "format_ratio",
    "format_signed_percent",
    "get_factor",
    "percent_number",
    "register_factor",
    "select_bucket",
    "signed_percent_number",
]

logger = logging.getLogger(__name__)

#: 参数类型枚举：``percent`` 为小数口径百分比（0.08 = 8%），``enum`` 为字符串枚举。
ParamType = Literal["float", "int", "bool", "percent", "enum"]

#: 值未落入任何声明档位时的兜底标签。
UNMATCHED_BUCKET = "其他"
#: 所需数据缺失、因子降级时的档位标签。
DEGRADED_BUCKET = "数据缺失"


# ============================================================ 标签格式化


def _round6(value: float) -> float:
    """保留 6 位小数，消除浮点乘法产生的尾差（如 0.05 * 100）。"""
    return round(float(value), 6)


def format_ratio(value: float) -> str:
    """比值标签：``0.6`` → ``"0.6"``，``1.0`` → ``"1.0"``，``1.05`` → ``"1.05"``。"""
    rounded = _round6(value)
    if rounded == int(rounded):
        return f"{rounded:.1f}"
    return f"{rounded:g}"


def format_count(value: float) -> str:
    """计数/百分比数值标签：``30.0`` → ``"30"``，``5.0`` → ``"5"``。"""
    rounded = _round6(value)
    if rounded == int(rounded):
        return f"{int(rounded)}"
    return f"{rounded:g}"


def format_percent(value: float) -> str:
    """小数口径百分比标签：``0.08`` → ``"8%"``，``0.05`` → ``"5%"``。"""
    return f"{percent_number(value)}%"


def percent_number(value: float) -> str:
    """小数口径百分比的数值部分（无 ``%``）：``0.08`` → ``"8"``，``-0.03`` → ``"-3"``。"""
    return format_count(float(value) * 100)


def format_signed_percent(value: float) -> str:
    """带符号百分比标签：``-0.03`` → ``"-3%"``，``0.03`` → ``"+3%"``，``0.0`` → ``"0%"``。"""
    return f"{signed_percent_number(value)}%"


def signed_percent_number(value: float) -> str:
    """带符号百分比的数值部分（无 ``%``）：``-0.03`` → ``"-3"``，``0.03`` → ``"+3"``。"""
    scaled = _round6(float(value) * 100)
    if scaled > 0:
        return f"+{format_count(scaled)}"
    return format_count(scaled)


# ============================================================ 参数 schema


@dataclass(frozen=True, slots=True)
class FactorParamSpec:
    """单个因子参数的声明（序列化后即为 ``factor_defs.params_schema`` 的一项）。

    Attributes:
        key: 参数键。
        label: 中文显示名。
        type: 参数类型（见 :data:`ParamType`）。
        default: 代码默认值；DB 无 active 配置且无运行时覆盖时使用。
        min: 允许下界（含），``None`` 表示不限制。
        max: 允许上界（含），``None`` 表示不限制。
        step: 前端步进值，``None`` 表示不指定。
        unit: 单位说明（如「小数」「分钟」「个」）。
        description: 参数说明，供前端表单与 Agent 展示。
    """

    key: str
    label: str
    type: ParamType
    default: float | int | bool | str
    min: float | int | None = None
    max: float | int | None = None
    step: float | None = None
    unit: str | None = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为前端可渲染的字典。"""
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "unit": self.unit,
            "description": self.description,
        }


# ============================================================ 档位定义


@dataclass(frozen=True, slots=True)
class Bucket:
    """声明式档位：既用于硬门槛判定，也用于有效性统计。

    两种形态：

    - 数值区间：给出 ``lo`` / ``hi``（``None`` 表示单边开放）与开闭标记；
    - 枚举取值：给出 ``equals``（此时忽略数值边界）。

    Attributes:
        label: 档位标签（如 ``">=8%"``）。
        lo: 下界；``None`` 表示 -inf。
        hi: 上界；``None`` 表示 +inf。
        lo_inclusive: 下界是否含（``[lo`` / ``(lo``）。
        hi_inclusive: 上界是否含（``hi]`` / ``hi)``）。
        equals: 枚举精确匹配值；非 ``None`` 时走字符串相等判定。
    """

    label: str
    lo: float | None = None
    hi: float | None = None
    lo_inclusive: bool = True
    hi_inclusive: bool = False
    equals: str | None = None

    def contains(self, value: float | str | None) -> bool:
        """判断取值是否落入本档位。"""
        if value is None:
            return False
        if self.equals is not None:
            return str(value) == self.equals
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        number = float(value)
        below_lo = self.lo is not None and (
            number < self.lo or (number == self.lo and not self.lo_inclusive)
        )
        if below_lo:
            return False
        above_hi = self.hi is not None and (
            number > self.hi or (number == self.hi and not self.hi_inclusive)
        )
        return not above_hi

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "label": self.label,
            "lo": self.lo,
            "hi": self.hi,
            "lo_inclusive": self.lo_inclusive,
            "hi_inclusive": self.hi_inclusive,
            "equals": self.equals,
        }


def _lo_key(bucket: Bucket) -> float:
    """档位下界的比较键（``None`` 视为 -inf）。"""
    return bucket.lo if bucket.lo is not None else float("-inf")


def select_bucket(value: float | str | None, buckets: Sequence[Bucket]) -> str:
    """返回 ``value`` 命中的档位标签；无命中返回 :data:`UNMATCHED_BUCKET`。

    命中多个档位时取**下界最大**者（阈值阶梯语义，如 ``>=3`` 与 ``>=4`` 同时命中取
    ``>=4``）；下界相同时取声明在前者。
    """
    best: Bucket | None = None
    for bucket in buckets:
        if not bucket.contains(value):
            continue
        if best is None or _lo_key(bucket) > _lo_key(best):
            best = bucket
    return best.label if best is not None else UNMATCHED_BUCKET


# ============================================================ 计算结果


@dataclass(frozen=True, slots=True)
class FactorResult:
    """单个因子的计算结果。

    Attributes:
        factor_id: 因子标识。
        value: 因子取值（数值因子为 ``float``，枚举因子为 ``str``；缺失为 ``None``）。
        bucket: 命中的档位标签（如 ``">=8%"``）。
        degraded: 是否因数据缺失而降级。
        detail: 计算所用的原始输入（字段快照，供可解释性与审计）。
    """

    factor_id: str
    value: float | str | None
    bucket: str
    degraded: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 化的字典。"""
        return {
            "factor_id": self.factor_id,
            "value": self.value,
            "bucket": self.bucket,
            "degraded": self.degraded,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FactorResult:
        """从 :meth:`to_dict` 的输出还原（供缓存读取）。"""
        return cls(
            factor_id=str(data["factor_id"]),
            value=data.get("value"),
            bucket=str(data.get("bucket", UNMATCHED_BUCKET)),
            degraded=bool(data.get("degraded", False)),
            detail=dict(data.get("detail") or {}),
        )


# ============================================================ 计算上下文


@dataclass(frozen=True, slots=True)
class Bar:
    """单日日线（单位：价格为元，``volume_shares`` 为股，与 readme §1.4 一致）。"""

    open: float
    high: float
    low: float
    close: float
    pre_close: float
    volume_shares: float


@dataclass(frozen=True, slots=True)
class MinutePoint:
    """单个分时点（``index`` 为分钟序号，``volume_lots`` 为手）。"""

    index: int
    time_label: str
    price: float
    volume_lots: float


@dataclass(slots=True)
class FactorContext:
    """因子计算所需的全部输入，由调用方（策略/引擎）预先装配。

    因子只读本对象，SHALL NOT 触达仓储或数据源，从而保持纯函数、可单测。

    Attributes:
        code: 股票代码。
        trade_date: 判定基准日 D（首阴日）。
        d_bar: D 日（首阴）日线。
        d_prev_bar: D 前一日日线（连板末板）。
        t_bar: D+1 全天日线。
        t1_bar: D+2 全天日线。
        minute_d: D 日分时序列。
        metrics: 预计算派生指标，键名沿用 readme §10 口径（如 ``d_amp_pct``、
            ``t.open_pct``、``mp_D.low_time_i``）。
    """

    code: str
    trade_date: date
    d_bar: Bar | None = None
    d_prev_bar: Bar | None = None
    t_bar: Bar | None = None
    t1_bar: Bar | None = None
    minute_d: tuple[MinutePoint, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def metric(self, key: str, *aliases: str) -> Any:
        """取派生指标：主键优先，其次别名；缺失返回 ``None``。"""
        for name in (key, *aliases):
            value = self.metrics.get(name)
            if value is not None:
                return value
        return None


# ============================================================ 因子基类


class FactorRegistryError(RuntimeError):
    """因子注册表异常（重复 ``factor_id`` / 未注册 / 缺少标识）。"""


FactorT = TypeVar("FactorT", bound="BaseFactor")


class BaseFactor(ABC):
    """因子基类。

    子类须声明类属性 ``factor_id`` / ``label`` / ``category`` / ``description`` /
    ``params_schema``，并实现 :meth:`compute`。
    """

    factor_id: ClassVar[str] = ""
    label: ClassVar[str] = ""
    category: ClassVar[str] = ""
    description: ClassVar[str] = ""
    params_schema: ClassVar[tuple[FactorParamSpec, ...]] = ()

    @abstractmethod
    def compute(self, ctx: FactorContext, params: Mapping[str, Any]) -> FactorResult:
        """按 ``ctx`` 与已解析 ``params`` 计算因子结果。

        Args:
            ctx: 已装配的计算上下文。
            params: 已解析参数（覆盖 > active 配置 > 代码默认）。

        Returns:
            含取值、命中档位、降级标记与字段快照的 :class:`FactorResult`。
        """
        raise NotImplementedError

    def buckets(self, params: Mapping[str, Any]) -> list[Bucket]:
        """返回本因子的档位定义；默认无档位。"""
        return []

    def default_params(self) -> dict[str, Any]:
        """返回全部参数的代码默认值。"""
        return {spec.key: spec.default for spec in self.params_schema}

    def param(self, params: Mapping[str, Any], key: str) -> Any:
        """取参数值，缺失时回退到该参数的 schema 默认值。

        Raises:
            KeyError: 因子未声明该参数（防止拼写错误静默取到错误阈值）。
        """
        for spec in self.params_schema:
            if spec.key == key:
                value = params.get(key, spec.default)
                return spec.default if value is None else value
        raise KeyError(f"{self.factor_id} 未声明参数 {key!r}")

    def classify(self, value: float | str | None, params: Mapping[str, Any]) -> str:
        """按档位定义判定取值所属档位标签。"""
        return select_bucket(value, self.buckets(params))

    def result(
        self,
        value: float | str | None,
        params: Mapping[str, Any],
        *,
        detail: Mapping[str, Any] | None = None,
        degraded: bool = False,
    ) -> FactorResult:
        """构造结果，自动判定档位。"""
        bucket = DEGRADED_BUCKET if degraded else self.classify(value, params)
        return FactorResult(
            factor_id=self.factor_id,
            value=value,
            bucket=bucket,
            degraded=degraded,
            detail=dict(detail or {}),
        )

    def missing(self, field: str, *, detail: Mapping[str, Any] | None = None) -> FactorResult:
        """构造「数据缺失、因子降级」的结果。"""
        payload: dict[str, Any] = {"missing_field": field}
        payload.update(detail or {})
        return FactorResult(
            factor_id=self.factor_id,
            value=None,
            bucket=DEGRADED_BUCKET,
            degraded=True,
            detail=payload,
        )

    def params_schema_dict(self) -> dict[str, Any]:
        """参数 schema 的 JSON 形态（写入 ``factor_defs.params_schema``）。"""
        return {
            "params": [spec.to_dict() for spec in self.params_schema],
            "buckets": [bucket.to_dict() for bucket in self.buckets(self.default_params())],
        }

    def schema_dict(self) -> dict[str, Any]:
        """完整 schema（供 :func:`app.factors.registry.export_schemas` 服务前端）。"""
        return {
            "factor_id": self.factor_id,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            **self.params_schema_dict(),
        }


# ============================================================ 注册表


_REGISTRY: dict[str, type[BaseFactor]] = {}


def register_factor(cls: type[FactorT]) -> type[FactorT]:
    """注册因子类（装饰器用法），导入即生效。

    Raises:
        FactorRegistryError: 缺少 ``factor_id`` 或 ``factor_id`` 重复。
    """
    if not cls.factor_id:
        raise FactorRegistryError(f"{cls.__name__} 缺少 factor_id")
    if cls.factor_id in _REGISTRY:
        existing = _REGISTRY[cls.factor_id]
        raise FactorRegistryError(
            f"重复的 factor_id {cls.factor_id!r}：{existing.__name__} 与 {cls.__name__}"
        )
    _REGISTRY[cls.factor_id] = cls
    return cls


def all_factors() -> list[type[BaseFactor]]:
    """返回全部已注册因子类（按 ``factor_id`` 升序）。"""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def get_factor(factor_id: str) -> type[BaseFactor]:
    """按标识取因子类。

    Raises:
        FactorRegistryError: 未注册。
    """
    try:
        return _REGISTRY[factor_id]
    except KeyError as exc:
        raise FactorRegistryError(
            f"未注册的因子: {factor_id!r}（已注册：{sorted(_REGISTRY)}）"
        ) from exc
