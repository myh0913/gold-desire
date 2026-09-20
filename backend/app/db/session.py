"""异步引擎与会话管理。

要点：

- 引擎 **惰性创建**：仅当首次需要连接时才实例化，导入本模块不触发任何 DB 连接。
- SQLite 使用 ``StaticPool`` + ``check_same_thread=False``（测试与本地默认，无需 PG）。
- PostgreSQL（asyncpg）使用可配置连接池。
- :func:`get_session` 为 FastAPI 依赖，提供请求级会话并保证提交/回滚/关闭。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _apply_sqlite_options(engine: AsyncEngine) -> None:
    """为 SQLite 引擎启用 WAL 并开启外键约束。"""
    sync_engine = engine.sync_engine
    if not sync_engine.url.drivername.startswith("sqlite"):
        return

    @event.listens_for(sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn: Any, _: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """惰性构建并返回全局异步引擎（进程内单例）。"""
    global _engine
    if _engine is None:
        resolved = settings or get_settings()
        kwargs: dict[str, Any] = {"echo": resolved.db_echo}
        if resolved.is_sqlite:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs.update(
                pool_size=resolved.db_pool_size,
                max_overflow=resolved.db_max_overflow,
                pool_timeout=resolved.db_pool_timeout,
                pool_pre_ping=True,
            )
        _engine = create_async_engine(resolved.database_url, **kwargs)
        _apply_sqlite_options(_engine)
    return _engine


def get_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """惰性构建会话工厂（依赖引擎）。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(settings),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：提供请求级会话并负责事务边界。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping_database(settings: Settings | None = None) -> bool:
    """数据库连通性探测，供 ``/ready`` 使用。"""
    engine = get_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def close_engine() -> None:
    """释放引擎连接池（应用 shutdown 调用）。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


__all__ = [
    "close_engine",
    "get_engine",
    "get_session",
    "get_session_factory",
    "ping_database",
]
