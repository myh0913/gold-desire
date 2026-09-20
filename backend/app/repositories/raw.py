"""原始响应留档仓储（``raw_*`` 语义）。

上游响应的原样落库，用于字段映射排障、来源审计与快照回放取证。
保留策略：``raw_responses`` 保留 30 天，由 :mod:`app.repositories.retention` 执行清理。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import delete, select

from app.models.raw import RawResponse
from app.repositories.base import BaseRepository


class RawResponseRepository(BaseRepository):
    """上游原始响应仓储。"""

    async def save(
        self,
        source: str,
        capability: str,
        args_hash: str,
        trade_date: date | None,
        payload: dict[str, Any],
        sha256: str,
        http_status: int | None = None,
        elapsed_ms: int | None = None,
    ) -> RawResponse:
        """留档一条原始响应，返回该行。

        同一 ``(source, capability, args_hash, trade_date)`` 只保留**最新**一条：
        先删同键旧行再插入，保证重复抓取不产生重复记录（无唯一约束下的幂等覆盖）。
        """
        await self.session.execute(
            delete(RawResponse).where(
                RawResponse.source == source,
                RawResponse.capability == capability,
                RawResponse.args_hash == args_hash,
                RawResponse.trade_date == trade_date,
            )
        )
        row = RawResponse(
            source=source,
            capability=capability,
            args_hash=args_hash,
            trade_date=trade_date,
            payload=payload,
            sha256=sha256,
            http_status=http_status,
            elapsed_ms=elapsed_ms,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_latest(self, source: str, capability: str, args_hash: str) -> RawResponse | None:
        """取某幂等键最近一次留档的原始响应。"""
        stmt = (
            select(RawResponse)
            .where(
                RawResponse.source == source,
                RawResponse.capability == capability,
                RawResponse.args_hash == args_hash,
            )
            .order_by(RawResponse.fetched_at.desc(), RawResponse.id.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def list_recent(self, limit: int = 50) -> list[RawResponse]:
        """按抓取时间倒序取最近留档。"""
        stmt = (
            select(RawResponse)
            .order_by(RawResponse.fetched_at.desc(), RawResponse.id.desc())
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


__all__ = ["RawResponseRepository"]
