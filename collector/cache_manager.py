import json
from typing import Any, Dict, Optional

import redis.asyncio as aioredis
from redis.asyncio import RedisError

from utils.logger import logger


HASH_KEY_PREFIX    = "collector:hash:"
PAYLOAD_KEY_PREFIX = "collector:payload:"


class CacheManager:
    def __init__(
        self,
        redis_client: aioredis.Redis,
        hash_ttl_seconds: int = 3600,
        payload_ttl_seconds: int = 300,
    ) -> None:
        self._redis = redis_client
        self._hash_ttl = hash_ttl_seconds
        self._payload_ttl = payload_ttl_seconds

    # ── Hash operations ──────────────────────────────────────────────────

    async def get_hash(self, product_id: str) -> Optional[str]:
        """
        Returns the stored StateHash for product_id, or None on miss/error.
        On Redis connection error: logs WARNING, returns None (triggers fallback).
        """
        key = self._hash_key(product_id)
        try:
            value = await self._redis.get(key)
            if value is None:
                return None
            # redis.asyncio returns bytes by default; decode if needed
            return value.decode("utf-8") if isinstance(value, bytes) else value
        except RedisError as exc:
            logger.bind(product_id=product_id, error=str(exc)).warning(
                "CacheManager.get_hash: Redis error, returning None"
            )
            return None

    async def set_hash(self, product_id: str, state_hash: str) -> None:
        """
        Atomically stores state_hash under collector:hash:{product_id} with EX TTL.
        Uses redis SET with ex= parameter (single atomic command).
        On Redis connection error: logs WARNING and returns without raising.
        """
        key = self._hash_key(product_id)
        try:
            await self._redis.set(key, state_hash, ex=self._hash_ttl)
        except RedisError as exc:
            logger.bind(product_id=product_id, error=str(exc)).warning(
                "CacheManager.set_hash: Redis error, hash not cached"
            )

    # ── Payload operations ───────────────────────────────────────────────

    async def get_payload(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns the cached API payload for product_id, or None.
        If cache value exists but cannot be JSON-decoded:
          - discards the key (redis.delete)
          - logs WARNING with product_id
          - returns None (caller will issue fresh API request)
        On Redis connection error: logs WARNING, returns None.
        """
        key = self._payload_key(product_id)
        try:
            value = await self._redis.get(key)
        except RedisError as exc:
            logger.bind(product_id=product_id, error=str(exc)).warning(
                "CacheManager.get_payload: Redis error, returning None"
            )
            return None

        if value is None:
            return None

        raw = value.decode("utf-8") if isinstance(value, bytes) else value
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            await self._redis.delete(key)
            logger.bind(product_id=product_id).warning(
                "CacheManager.get_payload: corrupt JSON in cache, entry deleted"
            )
            return None

    async def set_payload(self, product_id: str, payload: Dict[str, Any]) -> None:
        """
        Stores serialised payload under collector:payload:{product_id} with EX TTL.
        On Redis connection error: logs WARNING and returns without raising.
        """
        key = self._payload_key(product_id)
        try:
            serialised = json.dumps(payload)
            await self._redis.set(key, serialised, ex=self._payload_ttl)
        except RedisError as exc:
            logger.bind(product_id=product_id, error=str(exc)).warning(
                "CacheManager.set_payload: Redis error, payload not cached"
            )

    # ── Helpers ──────────────────────────────────────────────────────────

    def _hash_key(self, product_id: str) -> str:
        return f"{HASH_KEY_PREFIX}{product_id}"

    def _payload_key(self, product_id: str) -> str:
        return f"{PAYLOAD_KEY_PREFIX}{product_id}"
