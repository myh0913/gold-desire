"""涨停池加最大封单金额列；theme_stocks 删除从未写入的 selected_at 列

Revision ID: 0010_pool_seal_and_theme_cleanup
Revises: 0009_monitor_stock_details
Create Date: 2026-09-22

背景（总体 code review 结论的数据源字段补齐项）：

- ``limit_up_pool.seal_amount_yuan`` / ``amount_yuan`` 此前 100% NULL：主源
  xuangutong 池端点不提供封单金额/成交额。本次引入 hithink 第 8 轮补数
  （新能力 ``limit_up_pool_supplement``，合并回填封单金额/最大封单金额/首封
  时间）+ eltdx 分钟线成交额盘后聚合，故新增 ``max_seal_amount_yuan`` 列
  （hithink ``max_seal_money``，盘中最大封单额）。
- ``theme_stocks.selected_at`` 自建表起 100% NULL：上游不提供、全仓库无消费方
  （模型/schema/仓储列清单/前端类型四处声明均无读取逻辑），删除。

实现要点：

- ``max_seal_amount_yuan`` 为可选列（``nullable=True``），缺失写 NULL；
- ``selected_at`` 为纯删列（该列无索引、无约束，直接 drop）；
- 向后兼容：老数据不受影响，新列回填由采集任务自动完成。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_pool_seal_and_theme_cleanup"
down_revision: str | None = "0009_monitor_stock_details"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "limit_up_pool",
        sa.Column("max_seal_amount_yuan", sa.Numeric(20, 2), nullable=True),
    )
    op.drop_column("theme_stocks", "selected_at")


def downgrade() -> None:
    op.add_column(
        "theme_stocks",
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_column("limit_up_pool", "max_seal_amount_yuan")
