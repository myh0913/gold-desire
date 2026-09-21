"""采集完成事件：WS 薄事件广播 + 读缓存前缀失效。

**动机**：调度器（worker 进程）自动采集落库后，api 进程的读接口只能等
TTL（≤60s）过期才见到新数据，前端也收不到任何推送。本模块在**每个采集
任务成功后**做两件事：

1. 经 :mod:`app.core.ws_bus` 广播对应频道的**薄事件**（只带元信息，不带
   全量 payload——前端收到后失效查询、按需重取 REST，与旧 quant 的
   30s 全量推送模型相反）；
2. 按前缀删除共享 L2 缓存（Redis 下 api/worker 共享，即"写后失效"跨进程
   生效；无 Redis 时仅清进程内缓存，api 侧 L1 短 TTL（10~15s）兜底）。

两者均为**尽力而为**：任何异常只记日志，绝不影响采集结果。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.cache import get_cache

__all__ = ["CAPABILITY_EVENTS", "notify_ingest_completed"]

logger = logging.getLogger(__name__)


#: 能力 → (WS 频道 | None, 需失效的缓存命名空间)。无频道的能力只做缓存失效。
CAPABILITY_EVENTS: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "limit_up_pool": ("pool", ("pool",)),
    "ladder": ("pool", ("ladder",)),
    "market_sentiment": ("sentiment", ("sentiment_live", "sentiment_history")),
    "newsflash": ("newsflash", ("newsflash",)),
    "theme_rank": ("themes", ("theme",)),
    "theme_stocks": ("themes", ("theme",)),
    "daily_bars": (None, ("daily_bars", "minute_bars")),
    # minute_bars 供策略/分时图消费，无读缓存与 WS 事件；
    # opening_match 落 pool_snapshot，供 Phase.OPENING 读取，不产生事件。
    "minute_bars": (None, ("minute_bars",)),
    "opening_match": (None, ()),
    # trading_calendar / 无读缓存的能力不产生事件。
}


async def notify_ingest_completed(result: Any, trade_date: Any) -> None:
    """采集任务成功后广播 WS 事件并失效相关读缓存。

    Args:
        result: :class:`~app.ingest.pipeline.IngestResult`（鸭子类型，取
            ``capability`` / ``rows`` / ``source``）。
        trade_date: 目标交易日。
    """
    spec = CAPABILITY_EVENTS.get(str(getattr(result, "capability", "")))
    if spec is None:
        return
    channel, namespaces = spec

    for namespace in namespaces:
        try:
            await get_cache().delete_prefix(namespace)
        except Exception:  # pragma: no cover - 失效失败只记日志
            logger.warning(
                "ingest_cache_invalidate_failed",
                exc_info=True,
                extra={"namespace": namespace},
            )

    if channel is None:
        return
    from app.core.ws_bus import publish_event

    try:
        await publish_event(
            channel,
            {
                "source": f"ingest:{result.capability}",
                "trade_date": str(trade_date),
                "rows": int(getattr(result, "rows", 0) or 0),
            },
        )
    except Exception:  # pragma: no cover - 推送失败不影响采集
        logger.warning(
            "ingest_event_broadcast_failed",
            exc_info=True,
            extra={"channel": channel},
        )
