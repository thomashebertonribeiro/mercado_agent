import datetime
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config.settings import settings

# Async engine for database operations
# pool_pre_ping=True helps detect and recover from stale connections automatically
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
            
async def ping_database() -> bool:
    """Utility function to check if the database is reachable."""
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False

async def create_monthly_partitions(session: AsyncSession, target_date: datetime.date) -> None:
    """Creates PostgreSQL monthly partitions for all event-sourced tables if they don't exist."""
    import datetime
    from sqlalchemy import text
    from utils.logger import logger

    # Current month and next month boundaries
    first_day_current = target_date.replace(day=1)
    
    # Calculate first day of next month
    if first_day_current.month == 12:
        first_day_next = datetime.date(first_day_current.year + 1, 1, 1)
    else:
        first_day_next = datetime.date(first_day_current.year, first_day_current.month + 1, 1)
        
    current_suffix = first_day_current.strftime("%Y_%m")
    
    # Partition SQLs — 6 event tables + raw_payloads
    partitions = [
        {
            "name": f"price_events_{current_suffix}",
            "parent": "price_events",
            "from": first_day_current.isoformat(),
            "to": first_day_next.isoformat(),
        },
        {
            "name": f"product_events_{current_suffix}",
            "parent": "product_events",
            "from": first_day_current.isoformat(),
            "to": first_day_next.isoformat(),
        },
        {
            "name": f"seller_events_{current_suffix}",
            "parent": "seller_events",
            "from": first_day_current.isoformat(),
            "to": first_day_next.isoformat(),
        },
        {
            "name": f"ranking_events_{current_suffix}",
            "parent": "ranking_events",
            "from": first_day_current.isoformat(),
            "to": first_day_next.isoformat(),
        },
        {
            "name": f"review_events_{current_suffix}",
            "parent": "review_events",
            "from": first_day_current.isoformat(),
            "to": first_day_next.isoformat(),
        },
        {
            "name": f"question_events_{current_suffix}",
            "parent": "question_events",
            "from": first_day_current.isoformat(),
            "to": first_day_next.isoformat(),
        },
        {
            "name": f"raw_payloads_{current_suffix}",
            "parent": "raw_payloads",
            "from": first_day_current.isoformat(),
            "to": first_day_next.isoformat(),
        },
    ]
    
    for p in partitions:
        partition_name = p["name"]
        parent_table = p["parent"]
        val_from = p["from"]
        val_to = p["to"]
        
        # Check if table exists in PostgreSQL
        check_query = text(
            "SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = :tab);"
        )
        result = await session.execute(check_query, {"tab": partition_name})
        exists = result.scalar()
        
        if not exists:
            logger.info(f"Creating partition {partition_name} for range [{val_from} to {val_to})")
            create_query = text(
                f"CREATE TABLE IF NOT EXISTS {partition_name} PARTITION OF {parent_table} "
                f"FOR VALUES FROM ('{val_from}') TO ('{val_to}');"
            )
            await session.execute(create_query)
            await session.commit()

