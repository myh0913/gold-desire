"""minute_bars.amount_yuan 放开 NOT NULL（eltdx 分时不提供成交额）

Revision ID: 0003_minute_amount_optional
Revises: 0002_optional_columns
Create Date: 2026-09-21

背景：新增 ``minute_bars`` / ``opening_match`` 能力（eltdx TDX TCP 源，分时唯一
真实源）。eltdx 分钟点只提供 price / volume(手)，无成交额——按「能算的算、
算不出的留空」原则该列改 nullable（绝不估算填充）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_minute_amount_optional"
down_revision: str | None = "0002_optional_columns"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """把 minute_bars.amount_yuan 改为可空。"""
    with op.batch_alter_table("minute_bars") as batch:
        batch.alter_column(
            "amount_yuan", existing_type=sa.Numeric(precision=20, scale=2), nullable=True
        )


def downgrade() -> None:
    """回退为 NOT NULL（要求表中无 NULL，否则回退会失败）。"""
    with op.batch_alter_table("minute_bars") as batch:
        batch.alter_column(
            "amount_yuan", existing_type=sa.Numeric(precision=20, scale=2), nullable=False
        )
