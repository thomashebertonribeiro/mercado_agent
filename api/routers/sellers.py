import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import redis.asyncio as aioredis
from database.connection import get_db_session
from api.dependencies import get_redis_client
from models.product import Product
from repositories.seller import SellerRepository
from utils.logger import logger

router = APIRouter(prefix="/sellers", tags=["Sellers"])
STREAM_NAME = "ecommerce_tasks"

@router.post("/{seller_id}", status_code=status.HTTP_202_ACCEPTED)
async def monitor_seller(
    seller_id: int,
    redis_client: aioredis.Redis = Depends(get_redis_client)
) -> dict:
    """Registers a seller to monitor by pushing an enrichment task to Redis Stream."""
    task_payload = {"seller_id": str(seller_id)}
    await redis_client.xadd(
        STREAM_NAME,
        {"task_type": "crawl_seller", "payload": json.dumps(task_payload)}
    )
    logger.info(f"Manual crawl task queued for seller: {seller_id}")
    return {"message": f"Crawl task for seller {seller_id} queued successfully."}

@router.get("/{seller_id}")
async def get_seller_details(
    seller_id: int,
    db: AsyncSession = Depends(get_db_session)
) -> dict:
    """Fetches details for a registered seller and lists all their monitored products."""
    seller_repo = SellerRepository(db)
    seller = await seller_repo.get_by_id(seller_id)
    if not seller:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Seller {seller_id} is not registered or has not been crawled yet."
        )
        
    # Query products related to this seller
    query = select(Product).filter(Product.seller_id == seller_id)
    result = await db.execute(query)
    products = result.scalars().all()
    
    return {
        "id": seller.id,
        "nickname": seller.nickname,
        "registration_date": seller.registration_date,
        "created_at": seller.created_at,
        "updated_at": seller.updated_at,
        "monitored_products_count": len(products),
        "products": [
            {
                "id": p.id,
                "title": p.title,
                "category_id": p.category_id,
                "permalink": p.permalink
            } for p in products
        ]
    }
