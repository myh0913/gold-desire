"""按「能算的算、算不出的留空」放开可选列 NOT NULL 约束

Revision ID: 0002_optional_columns
Revises: 0001_initial
Create Date: 2026-09-19

背景：真实上游源提供的列并不一致，统一按「缺失写 NULL，不用 0 顶替」处理：

- ``hithink`` 涨停池不提供换手率 / 成交额 / 市值，日线不提供昨收；
- ``xuangutong`` 涨停池不提供封单金额；
- ``stage``（情绪周期阶段）任何单源都不提供，改由策略层状态机派生；
- ``themes`` / ``theme_stocks`` 的核心涨幅、成分数量、连板天数上游多为空。

因此把上述列改为 nullable；SQLite 无 ``ALTER COLUMN`` DROP NOT NULL 能力，
统一走 ``batch_alter_table``（PG 上等价普通 ALTER，SQLite 上自动重建表）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_optional_columns"
down_revision: str | None = "0001_initial"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_p12_4 = sa.Numeric(precision=12, scale=4)
_p10_4 = sa.Numeric(precision=10, scale=4)
_p20_2 = sa.Numeric(precision=20, scale=2)

#: (表名, ((列名, 列类型), ...))：需放开 NOT NULL 的列
_OPTIONAL_COLUMNS: tuple[tuple[str, tuple[tuple[str, sa.types.TypeEngine], ...]], ...] = (
    ("daily_bars", (("pre_close", _p12_4),)),
    (
        "limit_up_pool",
        (
            ("seal_amount_yuan", _p20_2),
            ("open_times", sa.Integer()),
            ("turnover_rate", _p10_4),
            ("amount_yuan", _p20_2),
            ("market_cap_yuan", _p20_2),
        ),
    ),
    (
        "market_sentiment",
        (("stage", sa.String(length=32)), ("max_continue_days", sa.Integer())),
    ),
    ("news_flash", (("level", sa.String(length=16)),)),
    ("themes", (("core_avg_pct", _p10_4), ("core_count", sa.Integer()))),
    ("theme_stocks", (("continue_days", sa.Integer()),)),
)


def upgrade() -> None:
    """把可选列改为可空。"""
    for table_name, columns in _OPTIONAL_COLUMNS:
        with op.batch_alter_table(table_name) as batch:
            for column, column_type in columns:
                batch.alter_column(column, existing_type=column_type, nullable=True)


def downgrade() -> None:
    """回退为 NOT NULL（要求表中无 NULL，否则回退会失败）。"""
    for table_name, columns in reversed(_OPTIONAL_COLUMNS):
        with op.batch_alter_table(table_name) as batch:
            for column, column_type in columns:
                batch.alter_column(column, existing_type=column_type, nullable=False)
