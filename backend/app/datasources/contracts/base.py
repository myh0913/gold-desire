"""能力契约基类、降级语义与校验失败模型。

设计要点：

- :class:`ContractModel` 是所有能力契约的基类，``extra="forbid"`` 保证
  **未映射的源字段无法泄漏**进契约对象；``frozen=True`` 使其不可变。
- :data:`DEGRADED` 是转换层「取不到合法值」的显式哨兵。转换函数失败时返回它，
  而不是静默回退成 ``0`` / ``""``——契约要求「宁可拒绝，不可错报」。
- :class:`ContractValidationError` 携带结构化的 :class:`ValidationIssue` 列表，
  供上层记录告警（源 / 能力 / 字段 / 原因 / 原始响应指针）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = [
    "DEGRADED",
    "ContractModel",
    "ContractValidationError",
    "ValidationIssue",
    "validate_records",
]


class ValidationIssue(BaseModel):
    """单条契约校验问题（可序列化，便于写告警/审计）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    capability: str
    field: str
    reason: str
    raw_pointer: str = ""


class ContractValidationError(Exception):
    """契约校验失败：记录被拒绝，SHALL NOT 用 0/空串填充。"""

    code: str = "contract_validation_error"

    def __init__(self, issues: Sequence[ValidationIssue], *, message: str | None = None) -> None:
        self.issues: list[ValidationIssue] = list(issues)
        if message is None:
            head = self.issues[0] if self.issues else None
            first = (
                f"（首个：{head.source}.{head.capability}.{head.field} {head.reason}）"
                if head
                else ""
            )
            message = f"契约校验失败：{len(self.issues)} 个字段非法" + first
        super().__init__(message)
        self.message = message

    def to_detail(self) -> dict[str, Any]:
        """返回结构化上下文，供 ``UpstreamError(detail=...)`` 或日志使用。"""
        return {"issues": [issue.model_dump() for issue in self.issues]}


class _DegradedSentinel:
    """``DEGRADED`` 单例类型：表示转换层无法产出合法值。"""

    _instance: _DegradedSentinel | None = None

    def __new__(cls) -> _DegradedSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<DEGRADED>"

    def __bool__(self) -> bool:
        return False


#: 转换失败哨兵：映射层见到它会构造 :class:`ValidationIssue` 并拒绝该记录。
DEGRADED: Final[_DegradedSentinel] = _DegradedSentinel()


class ContractModel(BaseModel):
    """能力契约基类：领域标准字段名 + 显式单位，禁止额外字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


def validate_records(
    model: type[ContractModel],
    rows: Sequence[dict[str, Any]],
    *,
    source: str,
    capability: str,
) -> list[ContractModel]:
    """把映射后的行批量校验为契约模型；任一行非法则整体抛错。

    Args:
        model: 目标契约模型类。
        rows: 已完成字段映射（键为契约字段名）的行。
        source: 源标识，写入问题上下文。
        capability: 能力名，写入问题上下文。

    Raises:
        ContractValidationError: 存在非法行（含类型/范围/额外字段错误）。
    """
    out: list[ContractModel] = []
    issues: list[ValidationIssue] = []
    for index, row in enumerate(rows):
        try:
            out.append(model.model_validate(row))
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(part) for part in err.get("loc", ())) or "<record>"
                issues.append(
                    ValidationIssue(
                        source=source,
                        capability=capability,
                        field=loc,
                        reason=str(err.get("msg", "invalid value")),
                        raw_pointer=f"records[{index}].{loc}",
                    )
                )
    if issues:
        raise ContractValidationError(issues)
    return out
