import redis.asyncio as aioredis
from typing import AsyncGenerator
from config.settings import settings
from database.connection import get_db_session

async def get_redis_client() -> AsyncGenerator[aioredis.Redis, None]:
    """Dependency injection helper to yield an active redis client."""
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        yield client
    finally:
        await client.close()
