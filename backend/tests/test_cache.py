"""缓存正确性测试：键含全部查询参数、命中/失效、优雅降级。

对应 spec「两级缓存 + 明确失效策略（写后失效 + TTL 兜底）」。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.repositories.config import StrategyDefRepository
from app.repositories.reads import ReadRepository
from app.services.cache_policy import CachePolicy, get_cache_policy, query_key

from tests.conftest import auth_header


class _RaisingCache:
    """所有操作都抛错的缓存后端（验证优雅降级）。"""

    async def get(self, key: str) -> Any:
        raise RuntimeError("cache get failed")

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        raise RuntimeError("cache set failed")

    async def delete(self, key: str) -> None:
        raise RuntimeError("cache delete failed")

    async def delete_prefix(self, prefix: str) -> int:
        raise RuntimeError("cache delete_prefix failed")

    async def close(self) -> None:
        raise RuntimeError("cache close failed")


def _count_calls(monkeypatch: pytest.MonkeyPatch, name: str) -> list[Any]:
    """包装 ``ReadRepository`` 的某个方法，记录调用次数。"""
    original = getattr(ReadRepository, name)
    calls: list[Any] = []

    async def _wrapper(self: ReadRepository, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await original(self, **kwargs)

    monkeypatch.setattr(ReadRepository, name, _wrapper)
    return calls


def test_query_key_includes_all_params() -> None:
    """缓存键必须包含全部查询参数（含分页），不同查询不得碰撞。"""
    base = query_key("stock", {"kw": None, "page": 1, "size": 20})
    assert base == "stock:page=1:size=20"
    assert query_key("stock", {"kw": "A", "page": 1, "size": 20}) != base
    assert query_key("stock", {"kw": None, "page": 2, "size": 20}) != base
    assert query_key("stock", {"kw": None, "page": 1, "size": 50}) != base


async def test_identical_query_hits_cache_once(
    client: httpx.AsyncClient,
    admin_token: str,
    market_seed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """相同查询两次 → 仓储只被调用一次。"""
    calls = _count_calls(monkeypatch, "paginate_stocks")
    headers = auth_header(admin_token)
    first = await client.get("/api/stocks", params={"page": 1}, headers=headers)
    second = await client.get("/api/stocks", params={"page": 1}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1


async def test_different_params_use_separate_keys(
    client: httpx.AsyncClient,
    admin_token: str,
    market_seed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同查询参数 → 不同缓存键 → 各自回源一次。"""
    calls = _count_calls(monkeypatch, "paginate_stocks")
    headers = auth_header(admin_token)
    await client.get("/api/stocks", params={"keyword": "测试一号"}, headers=headers)
    await client.get("/api/stocks", params={"keyword": "测试二号"}, headers=headers)
    assert len(calls) == 2


async def test_write_endpoint_invalidates_config_prefix(
    client: httpx.AsyncClient,
    admin_token: str,
    market_seed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """admin 写接口失效 ``config`` 前缀：写后再读会重新回源。"""
    original = StrategyDefRepository.list_all
    calls: list[int] = []

    async def _wrapper(self: StrategyDefRepository) -> Any:
        calls.append(1)
        return await original(self)

    monkeypatch.setattr(StrategyDefRepository, "list_all", _wrapper)
    headers = auth_header(admin_token)

    assert (await client.get("/api/strategies", headers=headers)).status_code == 200
    assert (await client.get("/api/strategies", headers=headers)).status_code == 200
    assert len(calls) == 1, "第二次读应命中缓存"

    saved = await client.put(
        "/api/strategies/dragon/config",
        json={"params": {"base_position": 0.31}, "note": "test"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text

    assert (await client.get("/api/strategies", headers=headers)).status_code == 200
    assert len(calls) == 2, "写后失效，必须重新回源"


async def test_cache_backend_failure_degrades_gracefully(
    client: httpx.AsyncClient, admin_token: str, market_seed: dict[str, Any]
) -> None:
    """缓存后端抛错时请求仍成功（落回 DB）。"""
    policy = get_cache_policy()
    policy._l2 = _RaisingCache()
    policy._l1 = _RaisingCache()

    response = await client.get("/api/stocks", headers=auth_header(admin_token))
    assert response.status_code == 200
    assert response.json()["total"] >= 1


async def test_cache_policy_ttl_table_is_data() -> None:
    """TTL 分级表存在且 L1 短于 L2。"""
    policy = CachePolicy(backend=_RaisingCache())
    assert policy.rule("sentiment_live").l2_ttl == 30
    assert policy.rule("pool").l2_ttl == 30
    assert policy.rule("ladder").l2_ttl == 60
    assert policy.rule("daily_bars").l2_ttl == 300
    assert policy.rule("sentiment_history").l2_ttl == 600
    assert policy.rule("config").l2_ttl == 300
    for namespace in ("pool", "ladder", "daily_bars", "config"):
        rule = policy.rule(namespace)
        assert rule.l1_ttl <= rule.l2_ttl


async def test_invalidate_removes_entries() -> None:
    """``invalidate(prefix)`` 清除该前缀下的条目。"""
    policy = get_cache_policy()
    key = query_key("pool", {"date": "2026-06-03"})
    await policy._l2.set(key, {"x": 1}, 30)
    await policy._l1.set(key, {"x": 1}, 5)
    removed = await policy.invalidate("pool")
    assert removed >= 1
    assert await policy._l2.get(key) is None
