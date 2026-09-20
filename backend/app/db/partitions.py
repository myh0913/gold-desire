"""PostgreSQL 月分区辅助（仅 PG 生效，SQLite 等方言自动跳过）。

spec「数据库与仓储层」要求行情类大表按 ``trade_date`` 做**月分区**：

- 参与分区的表见 :data:`PARTITIONED_TABLES`。
- 分区键均为 ``trade_date``（RANGE），分区名形如 ``daily_bars_202609``，
  另有 ``daily_bars_default`` 兜底分区承接越界日期。
- 非 PostgreSQL 方言（本地/测试的 SQLite）SHALL 直接跳过，保持普通表，不影响功能验证。

运行时用法（异步连接需经 ``run_sync`` 调用本模块的同步函数）::

    await conn.run_sync(lambda c: ensure_month_partition(c, "daily_bars", 2026, 10))
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class PartitionSpec:
    """分区父表在转换后需要重建的主键 / 唯一约束 / 索引。

    注意：PostgreSQL 要求分区表的唯一约束必须包含全部分区键，
    因此 ``primary_key`` 为 ``(id, trade_date)`` 而非仅 ``id``。
    """

    primary_key: tuple[str, ...] = ("id", "trade_date")
    unique: tuple[tuple[str, tuple[str, ...]], ...] = ()
    indexes: tuple[tuple[str, tuple[str, ...]], ...] = ()


PARTITION_SPECS: dict[str, PartitionSpec] = {
    "daily_bars": PartitionSpec(
        unique=(("uq_daily_bars_code_trade_date", ("code", "trade_date")),),
        indexes=(
            ("ix_daily_bars_trade_date", ("trade_date",)),
            ("ix_daily_bars_trade_date_code", ("trade_date", "code")),
        ),
    ),
    "minute_bars": PartitionSpec(
        unique=(("uq_minute_bars_code_trade_date_minute", ("code", "trade_date", "minute_index")),),
        indexes=(("ix_minute_bars_trade_date", ("trade_date",)),),
    ),
    "limit_up_pool": PartitionSpec(
        unique=(("uq_limit_up_pool_trade_date_pool_code", ("trade_date", "pool_type", "code")),),
        indexes=(
            ("ix_limit_up_pool_trade_date", ("trade_date",)),
            ("ix_limit_up_pool_continue_days", ("continue_days",)),
            ("ix_limit_up_pool_trade_date_continue_days", ("trade_date", "continue_days")),
        ),
    ),
    "pool_snapshot": PartitionSpec(
        unique=(("uq_pool_snapshot_trade_date_pool_name", ("trade_date", "pool_name")),),
    ),
    "market_sentiment": PartitionSpec(
        unique=(("uq_market_sentiment_trade_date", ("trade_date",)),),
    ),
}
"""表名 → 转换后需重建的约束与索引。"""

PARTITIONED_TABLES: tuple[str, ...] = tuple(PARTITION_SPECS)
"""参与月分区的表名（顺序固定）。"""

_PARTITION_MONTHS_BACK = 3
_PARTITION_MONTHS_FORWARD = 3


def _spec(table: str) -> PartitionSpec:
    """取表的分区规格，表名非法时直接报错（防止 DDL 注入）。"""
    try:
        return PARTITION_SPECS[table]
    except KeyError:  # pragma: no cover - 防御性分支
        raise ValueError(f"表 {table!r} 不在分区清单中：{PARTITIONED_TABLES}") from None


def _columns(names: tuple[str, ...]) -> str:
    """把列名元组渲染为带双引号的 SQL 列表。"""
    return ", ".join(f'"{name}"' for name in names)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """返回给定月份的 ``[起, 止)`` 日期边界。"""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def shift_month(day: date, months: int) -> date:
    """把日期按月平移 ``months`` 个月，返回目标月 1 号。"""
    index = (day.year * 12 + day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def partition_name(table: str, year: int, month: int) -> str:
    """月分区表名，如 ``daily_bars_202609``。"""
    return f"{table}_{year:04d}{month:02d}"


def default_partition_name(table: str) -> str:
    """兜底分区表名，如 ``daily_bars_default``。"""
    return f"{table}_default"


def ensure_month_partition(conn: Connection, table: str, year: int, month: int) -> str:
    """确保 ``table`` 的指定月分区存在，返回分区名。

    仅 PostgreSQL 执行 DDL；其他方言（SQLite）直接返回分区名而不做任何事。
    本函数为同步函数，异步连接请通过 ``conn.run_sync(...)`` 调用。
    """
    spec_name = partition_name(table, year, month)
    if conn.dialect.name != "postgresql":
        return spec_name
    _spec(table)
    start, end = month_bounds(year, month)
    conn.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{spec_name}" PARTITION OF "{table}" '
            f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
        )
    )
    return spec_name


def ensure_default_partition(conn: Connection, table: str) -> str:
    """确保兜底分区存在，返回分区名（非 PG 方言跳过）。"""
    name = default_partition_name(table)
    if conn.dialect.name != "postgresql":
        return name
    _spec(table)
    conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{name}" PARTITION OF "{table}" DEFAULT'))
    return name


def ensure_partitions_for_range(conn: Connection, table: str, start: date, end: date) -> list[str]:
    """确保 ``[start, end]`` 覆盖到的每个自然月分区都存在，返回分区名列表。"""
    cursor = shift_month(start, 0)
    last = shift_month(end, 0)
    created: list[str] = []
    while cursor <= last:
        created.append(ensure_month_partition(conn, table, cursor.year, cursor.month))
        cursor = shift_month(cursor, 1)
    return created


def ensure_partitions_around_today(
    conn: Connection,
    table: str,
    months_back: int = _PARTITION_MONTHS_BACK,
    months_forward: int = _PARTITION_MONTHS_FORWARD,
) -> list[str]:
    """确保「当前月 ± N 个月」的分区都存在（迁移与定时任务使用）。"""
    today = date.today()
    return ensure_partitions_for_range(
        conn, table, shift_month(today, -months_back), shift_month(today, months_forward)
    )


def convert_to_partitioned(conn: Connection, table: str) -> None:
    """把已存在的普通表转换为按 ``trade_date`` 月分区的分区父表。

    步骤：重命名旧表 → 建分区父表 → 重建主键/唯一约束/索引 → 建分区 → 迁移数据 → 删旧表。
    仅 PostgreSQL 执行；其他方言直接返回。
    """
    if conn.dialect.name != "postgresql":
        return
    spec = _spec(table)
    legacy = f"{table}__legacy"

    conn.execute(text(f'ALTER TABLE "{table}" RENAME TO "{legacy}"'))

    # 旧表的约束/索引会沿用原名称，与新建父表冲突，先释放。
    for name, _ in spec.indexes:
        conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
    conn.execute(text(f'ALTER TABLE "{legacy}" DROP CONSTRAINT IF EXISTS "{table}_pkey"'))
    for name, _ in spec.unique:
        conn.execute(text(f'ALTER TABLE "{legacy}" DROP CONSTRAINT IF EXISTS "{name}"'))

    # 分离自增序列：否则删除旧表时序列会被级联删除，新表默认值将失效。
    sequence = conn.execute(
        text("SELECT pg_get_serial_sequence(:tbl, 'id')"), {"tbl": legacy}
    ).scalar()
    if sequence:
        conn.execute(text(f"ALTER SEQUENCE {sequence} OWNED BY NONE"))

    conn.execute(
        text(
            f'CREATE TABLE "{table}" (LIKE "{legacy}" INCLUDING DEFAULTS INCLUDING CONSTRAINTS) '
            f"PARTITION BY RANGE (trade_date)"
        )
    )
    conn.execute(
        text(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{table}_pkey" '
            f"PRIMARY KEY ({_columns(spec.primary_key)})"
        )
    )
    for name, columns in spec.unique:
        conn.execute(
            text(f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" UNIQUE ({_columns(columns)})')
        )
    for name, columns in spec.indexes:
        conn.execute(text(f'CREATE INDEX "{name}" ON "{table}" ({_columns(columns)})'))

    ensure_partitions_around_today(conn, table)
    ensure_default_partition(conn, table)

    conn.execute(text(f'INSERT INTO "{table}" SELECT * FROM "{legacy}"'))
    if sequence:
        conn.execute(
            text(f"SELECT setval('{sequence}', COALESCE((SELECT MAX(id) FROM \"{table}\"), 1))")
        )
        conn.execute(text(f'ALTER SEQUENCE {sequence} OWNED BY "{table}".id'))
    conn.execute(text(f'DROP TABLE "{legacy}"'))


def partition_market_tables(conn: Connection) -> list[str]:
    """把全部行情大表转换为月分区表，返回已处理的表名（非 PG 方言返回空列表）。"""
    if conn.dialect.name != "postgresql":
        return []
    for table in PARTITIONED_TABLES:
        convert_to_partitioned(conn, table)
    return list(PARTITIONED_TABLES)


__all__ = [
    "PARTITIONED_TABLES",
    "PARTITION_SPECS",
    "PartitionSpec",
    "convert_to_partitioned",
    "default_partition_name",
    "ensure_default_partition",
    "ensure_month_partition",
    "ensure_partitions_around_today",
    "ensure_partitions_for_range",
    "month_bounds",
    "partition_market_tables",
    "partition_name",
    "shift_month",
]
