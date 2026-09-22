"""monitor_stocks 增加东财监管名单明细列（有效期 / 公告日 / 公告编号 / 原因分类 / 链接）

Revision ID: 0009_monitor_stock_details
Revises: 0008_advice_report_code
Create Date: 2026-09-22

背景：监管名单页此前只有「读」的一半——表 ``monitor_stocks`` 与接口都在，但**采集侧
完全没实现**（无契约 / 无 provider / 无映射 / 无任务），故页面永远为空。补采集侧时，
东财两个端点还提供了原先没接的明细字段：重点监控的**监控有效期**，异常波动的
**异动区间 / 公告日 / 公告编号 / 原因分类**。

实现要点：

- 全部为**可选列**（``nullable=True``），缺失写 NULL，不用 0 / 空串顶替；
- 纯增量迁移（只加列、不改既有列），向后兼容，老数据不受影响；
- ``link_url`` 仅重点监控名单携带（该端点部分条目自身也缺该键），异常波动端点无此字段。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_monitor_stock_details"
down_revision: str | None = "0008_advice_report_code"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: (列名, 类型)：监管名单新增明细字段（顺序即声明顺序）
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("start_date", sa.Date()),
    ("end_date", sa.Date()),
    ("notice_date", sa.Date()),
    ("info_code", sa.String(length=64)),
    ("reason_type", sa.String(length=128)),
    ("link_url", sa.String(length=512)),
)


def upgrade() -> None:
    """新增监管名单明细列（全部可空）。"""
    with op.batch_alter_table("monitor_stocks") as batch:
        for name, column_type in _NEW_COLUMNS:
            batch.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    """回滚：移除上述列。"""
    with op.batch_alter_table("monitor_stocks") as batch:
        for name, _ in reversed(_NEW_COLUMNS):
            batch.drop_column(name)
