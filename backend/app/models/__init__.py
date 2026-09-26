"""ORM 模型包。

按域拆分模块并在此统一 re-export，供仓储层与 Alembic 使用：

- :mod:`app.models.auth`：用户 / 角色 / 页面权限 / 邀请码 / 审计日志
- :mod:`app.models.market`：股票 / 日线 / 分时 / 涨停池 / 情绪 / 快讯 / 主题 / 天梯
- :mod:`app.models.config`：策略与因子的定义及参数版本 / 数据源注册表与健康度
- :mod:`app.models.derived`：建议报告 / 回测 / 采集任务 / Agent 会话审计
- :mod:`app.models.raw`：上游原始响应留档

导入本包即完成全部表到 :data:`app.db.base.Base.metadata` 的注册。
"""

from __future__ import annotations

from app.models.auth import AuditLog, Invitation, Role, RolePage, User
from app.models.config import (
    DatasourceHealth,
    DatasourceRegistry,
    FactorConfig,
    FactorDef,
    StrategyConfig,
    StrategyDef,
)
from app.models.derived import (
    AdviceMark,
    AdviceReport,
    AgentMessage,
    AgentSession,
    AgentToolCall,
    BacktestRun,
    DragonPoolCandidate,
    IngestJob,
)
from app.models.market import (
    DailyBar,
    LadderRow,
    LimitUpPool,
    MarketSentiment,
    MinuteBar,
    MonitorStock,
    NewsFlash,
    PoolSnapshot,
    Stock,
    Theme,
    ThemeStock,
)
from app.models.raw import RawResponse

__all__ = [
    "AdviceMark",
    "AdviceReport",
    "AgentMessage",
    "AgentSession",
    "AgentToolCall",
    "AuditLog",
    "BacktestRun",
    "DailyBar",
    "DatasourceHealth",
    "DatasourceRegistry",
    "DragonPoolCandidate",
    "FactorConfig",
    "FactorDef",
    "IngestJob",
    "Invitation",
    "LadderRow",
    "LimitUpPool",
    "MarketSentiment",
    "MinuteBar",
    "MonitorStock",
    "NewsFlash",
    "PoolSnapshot",
    "RawResponse",
    "Role",
    "RolePage",
    "Stock",
    "StrategyConfig",
    "StrategyDef",
    "Theme",
    "ThemeStock",
    "User",
]
