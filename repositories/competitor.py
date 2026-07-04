"""
repositories/competitor.py

Repository para operacoes de concorrentes no banco de dados.
"""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.competitor import Competitor
from models.competitor_history import CompetitorHistory


class CompetitorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_ml_item_id(self, ml_item_id: str) -> Optional[Competitor]:
        result = await self._session.execute(
            select(Competitor).where(Competitor.ml_item_id == ml_item_id)
        )
        return result.scalar_one_or_none()

    async def get_active_competitors(self) -> list[Competitor]:
        result = await self._session.execute(
            select(Competitor).where(Competitor.active == True).order_by(Competitor.created_at)
        )
        return list(result.scalars().all())

    async def create(
        self,
        ml_item_id: str,
        title: str = "",
        seller_id: int | None = None,
        category_id: str | None = None,
        current_price: float | None = None,
        original_price: float | None = None,
        available_quantity: int | None = None,
        status: str | None = None,
        listing_type: str | None = None,
        shipping_free: bool = False,
        permalink: str | None = None,
        thumbnail: str | None = None,
    ) -> Competitor:
        now = datetime.now(timezone.utc)
        competitor = Competitor(
            ml_item_id=ml_item_id,
            title=title,
            seller_id=seller_id,
            category_id=category_id,
            current_price=current_price,
            original_price=original_price,
            available_quantity=available_quantity,
            status=status,
            listing_type=listing_type,
            shipping_free=shipping_free,
            permalink=permalink,
            thumbnail=thumbnail,
            active=True,
            last_checked_at=now,
            created_at=now,
            updated_at=now,
        )
        self._session.add(competitor)
        await self._session.commit()
        await self._session.refresh(competitor)
        return competitor

    async def update_from_api(self, competitor: Competitor, item_data: dict) -> list[CompetitorHistory]:
        changes: list[CompetitorHistory] = []
        now = datetime.now(timezone.utc)

        new_price = float(item_data.get("price", 0))
        new_qty = item_data.get("available_quantity")
        new_status = item_data.get("status")
        new_shipping = item_data.get("shipping", {}).get("free_shipping", False)

        if competitor.current_price is not None and competitor.current_price != new_price:
            changes.append(CompetitorHistory(
                competitor_id=competitor.id,
                price=new_price,
                original_price=competitor.original_price,
                available_quantity=new_qty,
                status=new_status,
                change_type="price",
                change_detail=f"Preco alterado de {competitor.current_price} para {new_price}",
                recorded_at=now,
            ))

        if competitor.available_quantity is not None and new_qty is not None and competitor.available_quantity != new_qty:
            changes.append(CompetitorHistory(
                competitor_id=competitor.id,
                price=new_price,
                original_price=float(item_data.get("original_price") or 0) or None,
                available_quantity=new_qty,
                status=new_status,
                change_type="quantity",
                change_detail=f"Estoque alterado de {competitor.available_quantity} para {new_qty}",
                recorded_at=now,
            ))

        if competitor.status is not None and new_status is not None and competitor.status != new_status:
            changes.append(CompetitorHistory(
                competitor_id=competitor.id,
                price=new_price,
                available_quantity=new_qty,
                status=new_status,
                change_type="status",
                change_detail=f"Status alterado de '{competitor.status}' para '{new_status}'",
                recorded_at=now,
            ))

        if competitor.shipping_free != new_shipping:
            changes.append(CompetitorHistory(
                competitor_id=competitor.id,
                price=new_price,
                available_quantity=new_qty,
                status=new_status,
                shipping_free=new_shipping,
                change_type="shipping",
                change_detail=f"Frete gratis alterado de {competitor.shipping_free} para {new_shipping}",
                recorded_at=now,
            ))

        competitor.current_price = new_price
        competitor.original_price = float(item_data.get("original_price") or 0) or None
        competitor.available_quantity = new_qty
        competitor.status = new_status
        competitor.listing_type = item_data.get("listing_type_id")
        competitor.shipping_free = new_shipping
        competitor.title = item_data.get("title", competitor.title)
        competitor.last_checked_at = now
        competitor.updated_at = now

        for ch in changes:
            self._session.add(ch)

        await self._session.commit()
        return changes

    async def deactivate(self, competitor: Competitor) -> None:
        competitor.active = False
        competitor.updated_at = datetime.now(timezone.utc)
        await self._session.commit()

    async def count_active(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Competitor).where(Competitor.active == True)
        )
        return result.scalar() or 0

    async def get_recent_changes(self, limit: int = 50) -> list[CompetitorHistory]:
        result = await self._session.execute(
            select(CompetitorHistory).order_by(CompetitorHistory.recorded_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
