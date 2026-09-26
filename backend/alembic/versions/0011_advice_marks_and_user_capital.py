"""T-0022 产品改进包：advice_marks 已买入标记表 + users.capital_yuan 本金列

Revision ID: 0011_advice_marks_and_user_capital
Revises: 0010_pool_seal_and_theme_cleanup
Create Date: 2026-09-26

背景（产品评审改进包，用户拍板）：

- ``advice_marks``：建议页「已买入」人工标记，以 ``(trade_date, strategy_id,
  code)`` 唯一；取消买入 = 删行，表内恒为「已买入」集合。复盘页按同键
  串联展示 ``bought``。
- ``users.capital_yuan``：账户本金（元），用户自助维护；建议页按
  ``仓位 × 本金 ÷ 买点价`` 折算建议股数（前端计算，后端只存原始值）。

实现要点：

- 两项均为**纯增量**：新表无历史数据回填，新列全 NULL 起步，向后兼容。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_advice_marks_and_user_capital"
down_revision: str | None = "0010_pool_seal_and_theme_cleanup"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "advice_marks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="建议交易日"),
        sa.Column("strategy_id", sa.String(length=64), nullable=False, comment="策略标识"),
        sa.Column("code", sa.String(length=16), nullable=False, comment="股票代码"),
        sa.Column("marked_by", sa.String(length=64), nullable=True, comment="标记人用户名"),
        sa.Column(
            "marked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="标记时间",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trade_date", "strategy_id", "code", name="uq_advice_marks_date_strategy_code"
        ),
    )
    op.create_index("ix_advice_marks_trade_date", "advice_marks", ["trade_date"])
    op.add_column(
        "users",
        sa.Column("capital_yuan", sa.Numeric(14, 2), nullable=True, comment="账户本金（元）"),
    )


def downgrade() -> None:
    op.drop_column("users", "capital_yuan")
    op.drop_index("ix_advice_marks_trade_date", table_name="advice_marks")
    op.drop_table("advice_marks")
