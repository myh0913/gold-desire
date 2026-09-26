"""仓储层：数据库唯一读写出口。

- 各域仓储见 :mod:`app.repositories.market` / :mod:`app.repositories.config` /
  :mod:`app.repositories.derived` / :mod:`app.repositories.audit` / :mod:`app.repositories.raw`。
- :class:`Repositories` 聚合全部仓储为一个对象，供服务层与 Agent 依赖注入。
- :func:`get_repositories` 为 FastAPI 依赖，基于请求级会话构建容器。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.repositories.audit import (
    AgentMessageRepository,
    AgentSessionRepository,
    AgentToolCallRepository,
    AuditLogRepository,
)
from app.repositories.base import BaseRepository, PageResult
from app.repositories.config import (
    DatasourceHealthRepository,
    DatasourceRegistryRepository,
    FactorConfigRepository,
    FactorDefRepository,
    ParamDiff,
    StrategyConfigRepository,
    StrategyDefRepository,
)
from app.repositories.derived import (
    AdviceMarkRepository,
    AdviceReportRepository,
    BacktestRunRepository,
    CapabilityHealth,
    DragonPoolRepository,
    IngestJobRepository,
)
from app.repositories.market import (
    CycleJudgementRepository,
    DailyBarRepository,
    LadderRepository,
    LimitUpPoolRepository,
    MarketSentimentRepository,
    MinuteBarRepository,
    MonitorStockRepository,
    NewsFlashRepository,
    PoolSnapshotRepository,
    StockRepository,
    ThemeRepository,
)
from app.repositories.raw import RawResponseRepository

__all__ = [
    "AdviceMarkRepository",
    "AdviceReportRepository",
    "AgentMessageRepository",
    "AgentSessionRepository",
    "AgentToolCallRepository",
    "AuditLogRepository",
    "BacktestRunRepository",
    "BaseRepository",
    "CapabilityHealth",
    "DailyBarRepository",
    "DatasourceHealthRepository",
    "DatasourceRegistryRepository",
    "DragonPoolRepository",
    "FactorConfigRepository",
    "FactorDefRepository",
    "IngestJobRepository",
    "LadderRepository",
    "LimitUpPoolRepository",
    "MarketSentimentRepository",
    "MinuteBarRepository",
    "MonitorStockRepository",
    "NewsFlashRepository",
    "PageResult",
    "ParamDiff",
    "PoolSnapshotRepository",
    "RawResponseRepository",
    "Repositories",
    "StockRepository",
    "StrategyConfigRepository",
    "StrategyDefRepository",
    "ThemeRepository",
    "get_repositories",
]


@dataclass(slots=True)
class Repositories:
    """聚合全部仓储的容器，服务层 / Agent 通过单个对象按需访问各仓储。

    各仓储共享同一个 :class:`AsyncSession`，事务边界由该会话的持有者掌握。
    """

    session: AsyncSession
    stocks: StockRepository
    daily_bars: DailyBarRepository
    minute_bars: MinuteBarRepository
    limit_up_pool: LimitUpPoolRepository
    pool_snapshot: PoolSnapshotRepository
    dragon_pool: DragonPoolRepository
    market_sentiment: MarketSentimentRepository
    cycle_judgements: CycleJudgementRepository
    news_flash: NewsFlashRepository
    themes: ThemeRepository
    monitor_stocks: MonitorStockRepository
    ladder: LadderRepository
    strategy_configs: StrategyConfigRepository
    factor_configs: FactorConfigRepository
    strategy_defs: StrategyDefRepository
    factor_defs: FactorDefRepository
    datasource_registry: DatasourceRegistryRepository
    datasource_health: DatasourceHealthRepository
    advice_reports: AdviceReportRepository
    advice_marks: AdviceMarkRepository
    backtest_runs: BacktestRunRepository
    ingest_jobs: IngestJobRepository
    audit_logs: AuditLogRepository
    agent_sessions: AgentSessionRepository
    agent_messages: AgentMessageRepository
    agent_tool_calls: AgentToolCallRepository
    raw_responses: RawResponseRepository

    @classmethod
    def build(cls, session: AsyncSession) -> Repositories:
        """基于给定会话构建容器（各仓储共享同一会话）。"""
        return cls(
            session=session,
            stocks=StockRepository(session),
            daily_bars=DailyBarRepository(session),
            minute_bars=MinuteBarRepository(session),
            limit_up_pool=LimitUpPoolRepository(session),
            pool_snapshot=PoolSnapshotRepository(session),
            dragon_pool=DragonPoolRepository(session),
            market_sentiment=MarketSentimentRepository(session),
            cycle_judgements=CycleJudgementRepository(session),
            news_flash=NewsFlashRepository(session),
            themes=ThemeRepository(session),
            monitor_stocks=MonitorStockRepository(session),
            ladder=LadderRepository(session),
            strategy_configs=StrategyConfigRepository(session),
            factor_configs=FactorConfigRepository(session),
            strategy_defs=StrategyDefRepository(session),
            factor_defs=FactorDefRepository(session),
            datasource_registry=DatasourceRegistryRepository(session),
            datasource_health=DatasourceHealthRepository(session),
            advice_reports=AdviceReportRepository(session),
            advice_marks=AdviceMarkRepository(session),
            backtest_runs=BacktestRunRepository(session),
            ingest_jobs=IngestJobRepository(session),
            audit_logs=AuditLogRepository(session),
            agent_sessions=AgentSessionRepository(session),
            agent_messages=AgentMessageRepository(session),
            agent_tool_calls=AgentToolCallRepository(session),
            raw_responses=RawResponseRepository(session),
        )


async def get_repositories(session: AsyncSession = Depends(get_session)) -> Repositories:
    """FastAPI 依赖：基于请求级会话构建 :class:`Repositories` 容器。"""
    return Repositories.build(session)
