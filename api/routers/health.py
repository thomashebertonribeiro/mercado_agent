from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis
from database.connection import get_db_session
from api.dependencies import get_redis_client

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db_session),
    redis_client: aioredis.Redis = Depends(get_redis_client)
) -> dict:
    """Verifies connection health status of PostgreSQL and Redis."""
    db_ok = False
    redis_ok = False
    
    try:
        # Check database
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
        
    try:
        # Check Redis
        await redis_client.ping()
        redis_ok = True
    except Exception:
        pass
        
    status = "healthy" if db_ok and redis_ok else "unhealthy"
    return {
        "status": status,
        "database": "connected" if db_ok else "disconnected",
        "redis": "connected" if redis_ok else "disconnected"
    }
