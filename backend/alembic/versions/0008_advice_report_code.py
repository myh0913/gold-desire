"""advice_reports 唯一键加 code（修复同批多票建议互相覆盖）

Revision ID: 0008_advice_report_code
Revises: 0007_drop_news_level
Create Date: 2026-09-22

背景：旧唯一键 ``(trade_date, kind, strategy_id, ran_at)`` 中 ``ran_at`` 为整批统一
取值，同一次运行产出的多条建议（不同股票）键完全相同 → upsert 互相覆盖，
库里只剩最后一条（实测：upsert 返回 3、库中剩 1）。

修复：唯一键加入 ``code``（从 ``payload->>'code'`` 回填历史行）。

升级步骤（本迁移自动执行）：

1. 加 ``code`` 列（可空）；
2. 回填 ``code = payload->>'code'``（历史建议行 payload 均含 code）；
3. 删旧约束 ``uq_advice_reports_trade_kind_strategy_ran``，建新约束
   ``uq_advice_reports_trade_kind_strategy_ran_code``。

部署提示：上线时需执行 ``alembic upgrade head``。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_advice_report_code"
down_revision: str | None = "0007_drop_news_level"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_OLD_CONSTRAINT = "uq_advice_reports_trade_kind_strategy_ran"
_NEW_CONSTRAINT = "uq_advice_reports_trade_kind_strategy_ran_code"


def upgrade() -> None:
    """加 code 列 → 回填 → 换唯一约束。"""
    op.add_column("advice_reports", sa.Column("code", sa.String(length=16), nullable=True))
    op.execute("UPDATE advice_reports SET code = payload->>'code' WHERE code IS NULL")
    op.drop_constraint(_OLD_CONSTRAINT, "advice_reports", type_="unique")
    op.create_unique_constraint(
        _NEW_CONSTRAINT,
        "advice_reports",
        ["trade_date", "kind", "strategy_id", "ran_at", "code"],
    )


def downgrade() -> None:
    """回滚：还原旧唯一键（含 code 的新键先删），再删列。"""
    op.drop_constraint(_NEW_CONSTRAINT, "advice_reports", type_="unique")
    op.create_unique_constraint(
        _OLD_CONSTRAINT,
        "advice_reports",
        ["trade_date", "kind", "strategy_id", "ran_at"],
    )
    op.drop_column("advice_reports", "code")
