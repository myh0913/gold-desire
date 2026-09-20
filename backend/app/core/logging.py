"""结构化日志与 request_id 串联。

对外提供：

- ``request_id_var``：``ContextVar``，随请求生命周期绑定
- ``set_request_id`` / ``get_request_id``：读写辅助
- ``configure_logging``：一次性配置根 logger
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

from app.core.config import get_settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


def set_request_id(value: str | None) -> None:
    """绑定当前上下文的 request_id。"""
    request_id_var.set(value)


def get_request_id() -> str | None:
    """读取当前上下文的 request_id。"""
    return request_id_var.get()


class StructuredFormatter(logging.Formatter):
    """输出单行 JSON 的结构化日志格式化器。

    固定包含 timestamp / level / logger / request_id / message，
    其余 ``extra`` 字段原样附加。
    """

    def format(self, record: logging.LogRecord) -> str:
        """将日志记录序列化为单行 JSON。"""
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": get_request_id(),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str | None = None) -> None:
    """配置根 logger，幂等（重复调用不会叠加 handler）。

    Args:
        level: 显式日志级别；缺省时按 ``app_debug`` 取 DEBUG，否则 INFO。
    """
    settings = get_settings()
    resolved = (level or ("DEBUG" if settings.app_debug else "INFO")).upper()

    root = logging.getLogger()
    root.setLevel(resolved)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root.addHandler(handler)

    logging.getLogger("uvicorn.access").disabled = True
