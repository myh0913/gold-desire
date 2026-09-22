"""只读探针：任务3 相关的实测证据。

1. hithink limit-up-pool 端点当前真实返回字段（9-21 曾契约失败，须实测）；
2. eltdx 分钟线单票当日返回是否带 amount（gold-desire 映射注释称不提供，需验证）；
3. DB：limit_up_pool 两列 NULL 率 / stocks.list_date / theme_stocks.selected_at /
   raw_responses.elapsed_ms,http_status。
全部只读，不写任何表。
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date

from dotenv import load_dotenv

load_dotenv("/home/ubuntu/apps/gold-desire/.env")
os.chdir("/home/ubuntu/apps/gold-desire/backend")

import sys

sys.path.insert(0, ".")


async def probe_hithink() -> None:
    from app.datasources.providers.hithink import HithinkProvider

    provider = HithinkProvider()
    payload = await provider.fetch("limit_up_pool", date_ms=_date_ms(date.today()))
    items = payload.get("data", {}).get("item", [])
    print(f"[hithink limit_up_pool] rows={len(items)}")
    if items:
        print("  keys:", sorted(items[0].keys()))
        first = items[0]
        for k in ("seal_money", "max_seal_money", "limit_up_time", "thscode", "name"):
            print(f"  {k} = {first.get(k)!r}")


def _date_ms(d: date) -> int:
    import datetime

    return int(datetime.datetime(d.year, d.month, d.day, 0, 0, 0).timestamp() * 1000)


async def probe_eltdx() -> None:
    from app.datasources.providers.eltdx import EltdxProvider

    provider = EltdxProvider()
    code = "600519.SH"
    try:
        payload = await provider.fetch("minute_bars", thscode=code, date=date.today().isoformat())
    except Exception as exc:  # noqa: BLE001
        print(f"[eltdx minute_bars] fetch failed: {exc!r}")
        return
    items = payload.get("data", {}).get("item", payload.get("item", []))
    if isinstance(payload.get("data"), list):
        items = payload["data"]
    print(f"[eltdx minute_bars {code}] rows={len(items)}")
    if items:
        print("  keys:", sorted(items[0].keys()))
        print("  first:", json.dumps(items[0], ensure_ascii=False, default=str)[:300])


async def probe_db() -> None:
    import sqlalchemy as sa

    from app.core.config import get_settings

    settings = get_settings()

    engine = sa.create_engine(settings.database_url.replace("+asyncpg", "+psycopg"))
    with engine.connect() as conn:
        total = conn.execute(
            sa.text("SELECT count(*) FROM limit_up_pool")
        ).scalar()
        nulls = conn.execute(
            sa.text(
                "SELECT count(*) FILTER (WHERE seal_amount_yuan IS NULL),"
                " count(*) FILTER (WHERE amount_yuan IS NULL),"
                " count(*) FILTER (WHERE limit_up_time IS NULL) FROM limit_up_pool"
            )
        ).one()
        print(f"[db limit_up_pool] total={total} seal_null={nulls[0]} amount_null={nulls[1]} time_null={nulls[2]}")

        row = conn.execute(
            sa.text("SELECT count(*), count(list_date) FROM stocks")
        ).one()
        print(f"[db stocks] total={row[0]} list_date_present={row[1]}")

        row = conn.execute(
            sa.text("SELECT count(*), count(selected_at) FROM theme_stocks")
        ).one()
        print(f"[db theme_stocks] total={row[0]} selected_at_present={row[1]}")

        row = conn.execute(
            sa.text("SELECT count(*), count(elapsed_ms), count(http_status) FROM raw_responses")
        ).one()
        print(f"[db raw_responses] total={row[0]} elapsed_present={row[1]} http_status_present={row[2]}")

        row = conn.execute(
            sa.text("SELECT count(*), count(first_seal_time) FROM ladder")
        ).one()
        print(f"[db ladder] total={row[0]} first_seal_present={row[1]}")
    engine.dispose()


async def main() -> None:
    await probe_hithink()
    await probe_eltdx()
    await probe_db()


asyncio.run(main())
