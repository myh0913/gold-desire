"""删除 news_flash.level（源不提供重要级别）

Revision ID: 0007_drop_news_level
Revises: 0006_dragon_pool
Create Date: 2026-09-22

背景：快讯源（选股通 ``/api/v6/message/newsflash``）的 ``impact`` 字段实测恒为 0
——259 条样本（实时 199 + 历史存档 60）无一例外，``flash_message_type`` /
``is_premium`` / ``need_explained`` 亦均为常量。该列历史上只存过字符串 ``"0"``，
既无法展示也无法按级别筛选，因此连同列一起删除，不留死字段。

前端「7×24 快讯」页同步去掉了级别列与级别筛选（只保留关键词检索）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_drop_news_level"
down_revision: str | None = "0006_dragon_pool"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """删除 ``news_flash.level`` 列（该列无索引/约束，直接 drop）。"""
    with op.batch_alter_table("news_flash") as batch:
        batch.drop_column("level")


def downgrade() -> None:
    """回退：加回可空的 ``level`` 列。

    历史取值无法恢复（原值只是字符串 ``"0"``，无信息量），回退后一律为 NULL。
    """
    with op.batch_alter_table("news_flash") as batch:
        batch.add_column(sa.Column("level", sa.String(length=16), nullable=True))
