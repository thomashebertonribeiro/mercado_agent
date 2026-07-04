import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis
from database.connection import get_db_session
from api.dependencies import get_redis_client
from repositories.product import ProductRepository
from repositories.event import EventRepository
from utils.logger import logger

router = APIRouter(prefix="/products", tags=["Products"])
STREAM_NAME = "ecommerce_tasks"

@router.post("/{product_id}", status_code=status.HTTP_202_ACCEPTED)
async def monitor_product(
    product_id: str,
    redis_client: aioredis.Redis = Depends(get_redis_client)
) -> dict:
    """Registers a product ID to monitor by queuing a crawl task.

    The worker handles complete schema setup (seller and product) on consumption.
    """
    product_id = product_id.strip().upper()

    # Send crawl task to Redis Stream
    task_payload = {"product_id": product_id}
    await redis_client.xadd(
        STREAM_NAME,
        {"task_type": "crawl_product", "payload": json.dumps(task_payload)}
    )
    logger.info(f"Manual crawl task queued for product: {product_id}")

    return {"message": f"Crawl task for product {product_id} queued successfully."}

@router.get("/{product_id}")
async def get_product_details(
    product_id: str,
    db: AsyncSession = Depends(get_db_session)
) -> dict:
    """Fetches static metadata and latest crawled dynamic metrics for a product."""
    product_repo = ProductRepository(db)
    product = await product_repo.get_by_id(product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} is not registered or has not been crawled yet."
        )

    event_repo = EventRepository(db)
    latest_price = await event_repo.get_latest_price(product_id)

    return {
        "id": product.id,
        "title": product.title,
        "category_id": product.category_id,
        "seller_id": product.seller_id,
        "permalink": product.permalink,
        "created_at": product.created_at.isoformat() if product.created_at else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at else None,
        "latest_metrics": {
            "price": float(latest_price.price) if latest_price else None,
            "original_price": float(latest_price.original_price) if latest_price and latest_price.original_price else None,
            "available_quantity": latest_price.available_qty if latest_price else None,
            "captured_at": latest_price.occurred_at.isoformat() if latest_price else None,
        } if latest_price else None
    }

@router.get("/{product_id}/history")
async def get_product_history(
    product_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db_session)
) -> list:
    """Retrieves chronological price/stock history for a product."""
    product_repo = ProductRepository(db)
    product = await product_repo.get_by_id(product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found."
        )

    event_repo = EventRepository(db)
    history = await event_repo.get_price_history(product_id, limit)

    return [
        {
            "price": float(h.price),
            "original_price": float(h.original_price) if h.original_price else None,
            "available_quantity": h.available_qty,
            "captured_at": h.occurred_at.isoformat(),
        } for h in history
    ]
