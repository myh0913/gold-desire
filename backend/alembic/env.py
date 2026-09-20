"""Alembic 迁移环境（异步引擎）。

要点：

- DSN 统一来自 :func:`app.core.config.get_settings`，SHALL NOT 在 ``alembic.ini`` 写死。
- 导入 :mod:`app.models` 以把全部表注册进 ``Base.metadata``。
- SQLite（本地/测试）与 PostgreSQL 共用同一套迁移；PG 专属的分区 DDL 在
  revision 内部按方言判断后执行。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

import app.models  # noqa: F401  导入以注册全部表
from alembic import context
from app.core.config import get_settings
from app.db.base import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
# ``set_main_option`` 会做 % 插值，DSN 中的 % 需转义。
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL，不建立连接。"""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在同步连接上执行迁移（由异步连接 run_sync 进入）。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """以异步引擎执行在线迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式入口。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
