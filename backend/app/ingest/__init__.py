"""采集入库管道（采集侧）。

把上游调用移出用户请求路径：采集侧按能力注册任务、幂等入库、留档原始响应、记录
任务明细与健康度，并提供快照回放防未来函数与常驻调度器。

模块：

- :mod:`app.ingest.tasks`：任务注册表 + 各能力写入器（契约 → 目标表列）。
- :mod:`app.ingest.pipeline`：执行器（raw 留档 → 契约校验 → 幂等入库 → 任务留痕）。
- :mod:`app.ingest.scheduler`：常驻调度器（交易日历缓存、窗口、DB 幂等状态、维护）。
- :mod:`app.ingest.replay`：DB 快照回放防未来函数（``replay_scope`` / ``replay_resolve``）。
- :mod:`app.ingest.api_hooks`：管理接口钩子（手动触发 / 健康度）。
- :mod:`app.ingest.windows`：可配置时间窗口。

调度器（``app.ingest.scheduler``）不在本包 ``__init__`` 预导入，以便
``python -m app.ingest.scheduler`` 干净执行（避免 ``runpy`` 重复导入告警）；
请直接 ``from app.ingest.scheduler import IngestScheduler``。
"""

from __future__ import annotations

from app.ingest.api_hooks import ingest_health, trigger_task
from app.ingest.pipeline import IngestResult, run_many, run_task
from app.ingest.replay import replay_resolve, replay_scope
from app.ingest.tasks import (
    DEFAULT_TASKS,
    WRITERS,
    IngestTaskDef,
    all_tasks,
    get_task,
    register_task,
)
from app.ingest.windows import Window, load_windows

__all__ = [
    "DEFAULT_TASKS",
    "WRITERS",
    "IngestResult",
    "IngestTaskDef",
    "Window",
    "all_tasks",
    "get_task",
    "ingest_health",
    "load_windows",
    "register_task",
    "replay_resolve",
    "replay_scope",
    "run_many",
    "run_task",
    "trigger_task",
]
