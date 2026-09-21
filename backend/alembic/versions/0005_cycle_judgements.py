"""新增 cycle_judgements 表（情绪周期判定，每交易日一行）

Revision ID: 0005_cycle_judgements
Revises: 0004_pool_quant_fields
Create Date: 2026-09-21

背景：总览页要展示「情绪周期」横幅（对齐参考实现 quant）。判定口径照搬
``quant-system/src/quant_system/cycle.py``（用户 2026-09-07 确认的阈值集），
但结果需要**落库**才能：① 看历史；② 支持参考实现里的「放宽类状态（修复/加速）
需连续 2 个交易日确认」（``relaxed_needs_confirm``，要求能读到前一交易日的态）。

实现要点：

- 本表是**派生**数据，不是上游镜像：由 ``market_sentiment`` + ``limit_up_pool``
  计算得到，故独立成表，不往上游口径的 ``market_sentiment`` 里塞计算列；
- ``trade_date`` 唯一（每交易日一行），同日重跑幂等覆盖；
- ``reasons`` / ``indicators`` 为列表 / 字典，用 JSON 列承载；
- ``position_factor`` 可空——策略未声明门控时为 NULL（不臆造默认仓位）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_cycle_judgements"
down_revision: str | None = "0004_pool_quant_fields"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """建 ``cycle_judgements`` 表与唯一约束 / 索引。"""
    op.create_table(
        "cycle_judgements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("indicators", sa.JSON(), nullable=False),
        sa.Column("overheated", sa.Boolean(), nullable=False),
        sa.Column("relaxed_needs_confirm", sa.Boolean(), nullable=False),
        sa.Column("data_degraded", sa.Boolean(), nullable=False),
        sa.Column("position_factor", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        # ``SourceMixin`` 的两个公共列（溯源与重放）。
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", name="uq_cycle_judgements_trade_date"),
    )
    op.create_index(
        "ix_cycle_judgements_trade_date", "cycle_judgements", ["trade_date"], unique=False
    )


def downgrade() -> None:
    """回滚：删表（含索引）。"""
    op.drop_index("ix_cycle_judgements_trade_date", table_name="cycle_judgements")
    op.drop_table("cycle_judgements")
