"""
tests/test_intelligent_collector/test_cache_manager.py

Testes unitários do CacheManager.

Feature: intelligent-collector
Tests: 17.1 – 17.5
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st
from redis.exceptions import RedisError

from collector.cache_manager import CacheManager


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    return redis


def _make_cache(redis=None, hash_ttl=3600, payload_ttl=300) -> tuple[CacheManager, AsyncMock]:
    r = redis or _make_redis()
    return CacheManager(r, hash_ttl_seconds=hash_ttl, payload_ttl_seconds=payload_ttl), r


# ─────────────────────────────────────────────────────────────────────────────
# 17.1 — Property: key format e TTL corretos para hash
# Property 13: Cache key format and TTL are always correct
# ─────────────────────────────────────────────────────────────────────────────

@given(
    product_id=st.text(min_size=1, max_size=50),
    state_hash=st.text(min_size=1),
)
@h_settings(max_examples=100)
def test_set_hash_key_format_and_ttl(product_id: str, state_hash: str) -> None:
    """
    Property 13: Cache key format e TTL estão sempre corretos para hash.
    Validates: Requirements 5.1, 5.5
    """
    import asyncio

    redis = _make_redis()
    cache, _ = _make_cache(redis, hash_ttl=3600)

    asyncio.run(cache.set_hash(product_id, state_hash))

    expected_key = f"collector:hash:{product_id}"
    redis.set.assert_called_once_with(expected_key, state_hash, ex=3600)


# ─────────────────────────────────────────────────────────────────────────────
# 17.2 — Property: key format e TTL corretos para payload
# Property 13: Cache key format and TTL are always correct (payload)
# ─────────────────────────────────────────────────────────────────────────────

@given(
    product_id=st.text(min_size=1, max_size=50),
    payload=st.dictionaries(st.text(min_size=1), st.integers()),
)
@h_settings(max_examples=100)
def test_set_payload_key_format_and_ttl(product_id: str, payload: dict) -> None:
    """
    Property 13: Cache key format e TTL estão sempre corretos para payload.
    Validates: Requirements 5.2, 5.5
    """
    import asyncio

    redis = _make_redis()
    cache, _ = _make_cache(redis, payload_ttl=300)

    asyncio.run(cache.set_payload(product_id, payload))

    expected_key = f"collector:payload:{product_id}"
    expected_value = json.dumps(payload)
    redis.set.assert_called_once_with(expected_key, expected_value, ex=300)


# ─────────────────────────────────────────────────────────────────────────────
# 17.3 — JSON corrompido → chave deletada, WARNING logado, None retornado
# Validates: Requirements 5.4
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_payload_corrupted_json_deletes_key_and_returns_none() -> None:
    """
    Cache com JSON inválido → delete da chave, WARNING logado, retorno None.
    Validates: Requirements 5.4
    """
    redis = _make_redis()
    redis.get = AsyncMock(return_value=b"NOT_VALID_JSON{{{{")

    cache, _ = _make_cache(redis)

    with patch("collector.cache_manager.logger") as mock_logger:
        result = await cache.get_payload("product-abc")

    assert result is None
    redis.delete.assert_called_once_with("collector:payload:product-abc")
    mock_logger.bind.return_value.warning.assert_called_once()
    # Verifica que product_id aparece nos kwargs do log
    call_kwargs = mock_logger.bind.call_args
    assert "product-abc" in str(call_kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# 17.4 — Redis read error → WARNING logado, retorno None
# Validates: Requirements 5.6
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_payload_redis_error_returns_none() -> None:
    """
    RedisError no get → WARNING logado, método retorna None sem propagar.
    Validates: Requirements 5.6
    """
    redis = _make_redis()
    redis.get = AsyncMock(side_effect=RedisError("connection refused"))

    cache, _ = _make_cache(redis)

    with patch("collector.cache_manager.logger") as mock_logger:
        result = await cache.get_payload("product-xyz")

    assert result is None
    mock_logger.bind.return_value.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_hash_redis_error_returns_none() -> None:
    """
    RedisError no get_hash → WARNING logado, retorna None.
    Validates: Requirements 5.6
    """
    redis = _make_redis()
    redis.get = AsyncMock(side_effect=RedisError("timeout"))

    cache, _ = _make_cache(redis)

    with patch("collector.cache_manager.logger") as mock_logger:
        result = await cache.get_hash("product-fail")

    assert result is None
    mock_logger.bind.return_value.warning.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 17.5 — Redis write error → WARNING logado, sem exceção propagada
# Validates: Requirements 5.7
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_hash_redis_error_does_not_raise() -> None:
    """
    RedisError no set_hash → WARNING logado, método retorna normalmente.
    Validates: Requirements 5.7
    """
    redis = _make_redis()
    redis.set = AsyncMock(side_effect=RedisError("write failed"))

    cache, _ = _make_cache(redis)

    with patch("collector.cache_manager.logger") as mock_logger:
        # Não deve lançar exceção
        await cache.set_hash("product-abc", "sha256abc")

    mock_logger.bind.return_value.warning.assert_called_once()


@pytest.mark.asyncio
async def test_set_payload_redis_error_does_not_raise() -> None:
    """
    RedisError no set_payload → WARNING logado, método retorna normalmente.
    Validates: Requirements 5.7
    """
    redis = _make_redis()
    redis.set = AsyncMock(side_effect=RedisError("write failed"))

    cache, _ = _make_cache(redis)

    with patch("collector.cache_manager.logger") as mock_logger:
        await cache.set_payload("product-abc", {"price": 100})

    mock_logger.bind.return_value.warning.assert_called_once()
