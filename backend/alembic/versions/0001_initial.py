"""initial schema：全量建表 + PostgreSQL 月分区

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-19

覆盖 spec「数据库与仓储层」的全部表：

- auth：``roles`` / ``users`` / ``role_pages`` / ``invitations`` / ``audit_logs``
- market（std）：``stocks`` / ``daily_bars`` / ``minute_bars`` / ``limit_up_pool`` /
  ``pool_snapshot`` / ``market_sentiment`` / ``news_flash`` / ``themes`` /
  ``theme_stocks`` / ``monitor_stocks`` / ``ladder_rows``
- config：``strategy_defs`` / ``strategy_configs`` / ``factor_defs`` / ``factor_configs`` /
  ``datasource_registry`` / ``datasource_health``
- derived：``advice_reports`` / ``backtest_runs`` / ``ingest_jobs`` /
  ``agent_sessions`` / ``agent_messages`` / ``agent_tool_calls``
- raw：``raw_responses``

建表后，仅当方言为 PostgreSQL 时，把行情大表转换为按 ``trade_date`` 的月分区表；
SQLite 等方言保持普通表（便于本地与 CI 验证）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.db.partitions import partition_market_tables
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# 跨方言 JSON：PG 落 JSONB，其余落 JSON
_json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_dt = sa.DateTime(timezone=True)
_now = sa.func.now()


def upgrade() -> None:
    """建全部表，并在 PostgreSQL 上完成月分区转换。"""
    _create_auth_tables()
    _create_market_tables()
    _create_config_tables()
    _create_derived_tables()
    _create_raw_tables()

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        partition_market_tables(bind)


def downgrade() -> None:
    """按依赖倒序删除全部表（分区父表被删除时其分区随之删除）。"""
    op.drop_table("raw_responses")

    op.drop_table("agent_tool_calls")
    op.drop_table("agent_messages")
    op.drop_table("agent_sessions")
    op.drop_table("ingest_jobs")
    op.drop_table("backtest_runs")
    op.drop_table("advice_reports")

    op.drop_table("datasource_health")
    op.drop_table("datasource_registry")
    op.drop_table("factor_configs")
    op.drop_table("factor_defs")
    op.drop_table("strategy_configs")
    op.drop_table("strategy_defs")

    op.drop_table("ladder_rows")
    op.drop_table("monitor_stocks")
    op.drop_table("theme_stocks")
    op.drop_table("themes")
    op.drop_table("news_flash")
    op.drop_table("market_sentiment")
    op.drop_table("pool_snapshot")
    op.drop_table("limit_up_pool")
    op.drop_table("minute_bars")
    op.drop_table("daily_bars")
    op.drop_table("stocks")

    op.drop_table("audit_logs")
    op.drop_table("invitations")
    op.drop_table("role_pages")
    op.drop_table("users")
    op.drop_table("roles")


# --------------------------------------------------------------------- auth


def _create_auth_tables() -> None:
    """认证与授权相关表。"""
    op.create_table(
        "roles",
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_login_at", _dt, nullable=True),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.Column("updated_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_role", "users", ["role"], unique=False)

    op.create_table(
        "role_pages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("page_key", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["role"], ["roles.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role", "page_key", name="uq_role_pages_role_page"),
    )
    op.create_index("ix_role_pages_role", "role_pages", ["role"], unique=False)
    op.create_index("ix_role_pages_page_key", "role_pages", ["page_key"], unique=False)

    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("expires_at", _dt, nullable=True),
        sa.Column("used_by", sa.String(length=64), nullable=True),
        sa.Column("used_at", _dt, nullable=True),
        sa.Column("revoked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invitations_code", "invitations", ["code"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", _dt, server_default=_now, nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=128), nullable=True),
        sa.Column("detail", _json, nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_ts", "audit_logs", ["ts"], unique=False)


# ------------------------------------------------------------------- market


def _create_market_tables() -> None:
    """标准化行情/情绪/资讯表（大表后续在 PG 上转为月分区）。"""
    op.create_table(
        "stocks",
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("board", sa.String(length=32), nullable=False),
        sa.Column("is_st", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("list_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.Column("updated_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )

    op.create_table(
        "daily_bars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("high", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("low", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("close", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("pre_close", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("volume_shares", sa.BigInteger(), nullable=False),
        sa.Column("amount_yuan", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "trade_date", name="uq_daily_bars_code_trade_date"),
    )
    op.create_index("ix_daily_bars_trade_date", "daily_bars", ["trade_date"], unique=False)
    op.create_index(
        "ix_daily_bars_trade_date_code", "daily_bars", ["trade_date", "code"], unique=False
    )

    op.create_table(
        "minute_bars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("minute_index", sa.Integer(), nullable=False),
        sa.Column("time_label", sa.String(length=5), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("volume_lots", sa.BigInteger(), nullable=False),
        sa.Column("amount_yuan", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "code", "trade_date", "minute_index", name="uq_minute_bars_code_trade_date_minute"
        ),
    )
    op.create_index("ix_minute_bars_trade_date", "minute_bars", ["trade_date"], unique=False)

    op.create_table(
        "limit_up_pool",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("continue_days", sa.Integer(), nullable=False),
        sa.Column("limit_up_time", sa.String(length=5), nullable=True),
        sa.Column("seal_amount_yuan", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("open_times", sa.Integer(), nullable=False),
        sa.Column("turnover_rate", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("amount_yuan", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("market_cap_yuan", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("pool_type", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trade_date", "pool_type", "code", name="uq_limit_up_pool_trade_date_pool_code"
        ),
    )
    op.create_index("ix_limit_up_pool_trade_date", "limit_up_pool", ["trade_date"], unique=False)
    op.create_index(
        "ix_limit_up_pool_continue_days", "limit_up_pool", ["continue_days"], unique=False
    )
    op.create_index(
        "ix_limit_up_pool_trade_date_continue_days",
        "limit_up_pool",
        ["trade_date", "continue_days"],
        unique=False,
    )

    op.create_table(
        "pool_snapshot",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("pool_name", sa.String(length=64), nullable=False),
        sa.Column("payload", _json, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trade_date", "pool_name", name="uq_pool_snapshot_trade_date_pool_name"
        ),
    )

    op.create_table(
        "market_sentiment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("temperature", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("limit_up_count", sa.Integer(), nullable=False),
        sa.Column("limit_down_count", sa.Integer(), nullable=False),
        sa.Column("broken_board_count", sa.Integer(), nullable=False),
        sa.Column("broken_rate", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("up_count", sa.Integer(), nullable=False),
        sa.Column("down_count", sa.Integer(), nullable=False),
        sa.Column("max_continue_days", sa.Integer(), nullable=False),
        sa.Column("premium_rate", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", name="uq_market_sentiment_trade_date"),
    )

    op.create_table(
        "news_flash",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", _dt, nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("symbols", _json, nullable=False),
        sa.Column("categories", _json, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ts", "title", name="uq_news_flash_ts_title"),
    )
    op.create_index("ix_news_flash_ts", "news_flash", ["ts"], unique=False)

    op.create_table(
        "themes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("core_avg_pct", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("core_count", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", "name", name="uq_theme_trade_date_name"),
    )

    op.create_table(
        "theme_stocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("theme_name", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("pct", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("turnover_rate", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("continue_days", sa.Integer(), nullable=False),
        sa.Column("selected_at", _dt, nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trade_date", "theme_name", "code", name="uq_theme_stock_trade_date_theme_code"
        ),
    )

    op.create_table(
        "monitor_stocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trade_date", "kind", "code", name="uq_monitor_stock_trade_date_kind_code"
        ),
    )

    op.create_table(
        "ladder_rows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("continue_days", sa.Integer(), nullable=False),
        sa.Column("first_seal_time", sa.String(length=5), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingested_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", "code", name="uq_ladder_row_trade_date_code"),
    )
    op.create_index(
        "ix_ladder_rows_trade_date_continue_days",
        "ladder_rows",
        ["trade_date", "continue_days"],
        unique=False,
    )


# ------------------------------------------------------------------- config


def _create_config_tables() -> None:
    """策略/因子定义与版本化配置、数据源注册表与健康度。"""
    op.create_table(
        "strategy_defs",
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("params_schema", _json, nullable=False),
        sa.Column("gate_matrix", _json, nullable=False),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.Column("updated_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("strategy_id"),
    )

    op.create_table(
        "strategy_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("params", _json, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_configs_strategy_version"),
    )
    op.create_index("ix_strategy_configs_strategy_id", "strategy_configs", ["strategy_id"])
    op.create_index(
        "ix_strategy_configs_strategy_id_status",
        "strategy_configs",
        ["strategy_id", "status"],
    )

    op.create_table(
        "factor_defs",
        sa.Column("factor_id", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("params_schema", _json, nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.Column("updated_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("factor_id"),
    )

    op.create_table(
        "factor_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("factor_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("params", _json, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("factor_id", "version", name="uq_factor_configs_factor_version"),
    )
    op.create_index("ix_factor_configs_factor_id", "factor_configs", ["factor_id"])
    op.create_index("ix_factor_configs_factor_id_status", "factor_configs", ["factor_id", "status"])

    op.create_table(
        "datasource_registry",
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("capabilities", _json, nullable=False),
        sa.Column("rate_limit_per_min", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.Column("updated_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("source_id"),
    )

    op.create_table(
        "datasource_health",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("ok", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("detail", _json, nullable=True),
        sa.Column("checked_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_datasource_health_checked_at", "datasource_health", ["checked_at"])
    op.create_index(
        "ix_datasource_health_source_capability_checked_at",
        "datasource_health",
        ["source_id", "capability", "checked_at"],
    )


# ------------------------------------------------------------------ derived


def _create_derived_tables() -> None:
    """建议报告、回测、采集任务与 Agent 审计表。"""
    op.create_table(
        "advice_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.Integer(), nullable=True),
        sa.Column("payload", _json, nullable=False),
        sa.Column("ran_at", _dt, nullable=False),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trade_date",
            "kind",
            "strategy_id",
            "ran_at",
            name="uq_advice_reports_trade_kind_strategy_ran",
        ),
    )
    op.create_index("ix_advice_reports_trade_date", "advice_reports", ["trade_date"])
    op.create_index("ix_advice_reports_strategy_id", "advice_reports", ["strategy_id"])

    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("strategies", _json, nullable=False),
        sa.Column("params", _json, nullable=False),
        sa.Column("report", _json, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", _dt, nullable=True),
        sa.Column("finished_at", _dt, nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_runs_run_id", "backtest_runs", ["run_id"], unique=True)

    op.create_table(
        "ingest_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", _dt, nullable=True),
        sa.Column("finished_at", _dt, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingest_jobs_job_id", "ingest_jobs", ["job_id"], unique=True)
    op.create_index("ix_ingest_jobs_capability", "ingest_jobs", ["capability"])
    op.create_index("ix_ingest_jobs_status", "ingest_jobs", ["status"])
    op.create_index(
        "ix_ingest_jobs_capability_trade_date", "ingest_jobs", ["capability", "trade_date"]
    )

    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.Column("updated_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_sessions_session_id", "agent_sessions", ["session_id"], unique=True)

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", _json, nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_messages_session_id", "agent_messages", ["session_id"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("arguments", _json, nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("ok", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("denied", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_tool_calls_session_id", "agent_tool_calls", ["session_id"])


# ---------------------------------------------------------------------- raw


def _create_raw_tables() -> None:
    """上游原始响应留档表（保留 30 天，由采集侧清理任务执行）。"""
    op.create_table(
        "raw_responses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("payload", _json, nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("fetched_at", _dt, server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_raw_responses_source", "raw_responses", ["source"])
    op.create_index("ix_raw_responses_capability", "raw_responses", ["capability"])
    op.create_index("ix_raw_responses_fetched_at", "raw_responses", ["fetched_at"])
    op.create_index(
        "ix_raw_responses_source_capability_trade_date",
        "raw_responses",
        ["source", "capability", "trade_date"],
    )
