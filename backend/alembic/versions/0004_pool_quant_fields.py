"""limit_up_pool 增加 quant 对齐字段（现价/涨幅/量比/流通市值/封单比/原因/板块/时间线）

Revision ID: 0004_pool_quant_fields
Revises: 0003_minute_amount_optional
Create Date: 2026-09-21

背景：涨停池页对齐参考实现（quant）的展示口径，需要上游选股通 ``pool/detail``
提供的以下字段——现价、涨跌幅、量比、流通市值、封单比、涨停原因、关联板块、
封板时间线。同一接口的 7 种池型（``pool_name``）也由同一张表承载（``pool_type``）。

实现要点：

- 全部为**可选列**（``nullable=True``），缺失写 NULL，不用 0 顶替；
- 纯增量迁移（只加列、不改既有列），向后兼容，老数据不受影响；
- ``plates`` / ``timeline`` 为嵌套结构，用 JSON 列承载；
- ``limit_up_pool`` 是月分区表：PG 上 ``ALTER TABLE ADD COLUMN`` 会自动传播到
  各分区（无需逐分区处理）；SQLite 走 ``batch_alter_table`` 重建表。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_pool_quant_fields"
down_revision: str | None = "0003_minute_amount_optional"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: (列名, 类型)：涨停池新增的 quant 对齐字段（顺序即声明顺序）
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("price", sa.Numeric(precision=12, scale=4)),
    ("change_pct", sa.Numeric(precision=10, scale=4)),
    ("volume_bias_ratio", sa.Numeric(precision=10, scale=4)),
    ("free_cap_yuan", sa.Numeric(precision=20, scale=2)),
    ("seal_ratio", sa.Numeric(precision=14, scale=8)),
    ("reason", sa.Text()),
    ("plates", sa.JSON()),
    ("timeline", sa.JSON()),
)


def upgrade() -> None:
    """新增涨停池的 quant 对齐字段（全部可空）。"""
    with op.batch_alter_table("limit_up_pool") as batch:
        for name, column_type in _NEW_COLUMNS:
            batch.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    """回滚：移除上述字段。"""
    with op.batch_alter_table("limit_up_pool") as batch:
        for name, _ in reversed(_NEW_COLUMNS):
            batch.drop_column(name)
