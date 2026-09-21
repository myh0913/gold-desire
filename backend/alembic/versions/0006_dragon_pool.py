"""新增 dragon_pool 表（龙回头盘后建池候选，快照语义）

Revision ID: 0006_dragon_pool
Revises: 0005_cycle_judgements
Create Date: 2026-09-22

背景：``Phase.POOL``（盘后建池）此前只把候选经 WS 推送、不落库，「量化选股」页
无法在收盘后回看「给第二天做参考的股票」。用户 2026-09-22 决策：盘后建池需要落库。

实现要点：

- 本表是**派生**数据：由 ``daily_bars`` / ``minute_bars`` 经策略样本推导，独立成表；
- 快照语义：同一 ``(trade_date, strategy_id)`` 重跑整体替换（先删后插），
  故无需唯一约束，只建 ``(trade_date, strategy_id)`` 组合索引供查询；
- ``d_amp_pct`` / ``shape_label`` 可空——样本字段缺失时不臆造。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_dragon_pool"
down_revision: str | None = "0005_cycle_judgements"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """建 ``dragon_pool`` 表与查询索引。"""
    op.create_table(
        "dragon_pool",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("d_date", sa.Date(), nullable=False),
        sa.Column("boards", sa.Integer(), nullable=False),
        sa.Column("d_amp_pct", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("shape_label", sa.String(length=32), nullable=True),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dragon_pool_date_strategy",
        "dragon_pool",
        ["trade_date", "strategy_id"],
        unique=False,
    )


def downgrade() -> None:
    """回滚：删表（含索引）。"""
    op.drop_index("ix_dragon_pool_date_strategy", table_name="dragon_pool")
    op.drop_table("dragon_pool")
