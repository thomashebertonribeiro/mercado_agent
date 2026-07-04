"""
repositories/signal.py

Repositorio para Signals gerados pela Intelligence Engine.
"""

from typing import Optional, Sequence

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models.signal import Signal
from repositories.base import BaseRepository


class SignalRepository(BaseRepository[Signal]):
    """Acesso a tabela de sinais."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Signal, session)

    async def get_by_product(
        self,
        product_id: str,
        signal_type: Optional[str] = None,
        limit: int = 50,
    ) -> Sequence[Signal]:
        """Retorna sinais recentes de um produto."""
        query = (
            select(Signal)
            .where(Signal.product_id == product_id)
            .order_by(desc(Signal.computed_at))
            .limit(limit)
        )
        if signal_type:
            query = query.where(Signal.signal_type == signal_type)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_by_category(
        self,
        category_id: str,
        signal_type: Optional[str] = None,
        limit: int = 50,
    ) -> Sequence[Signal]:
        """Retorna sinais recentes de uma categoria."""
        query = (
            select(Signal)
            .where(Signal.category_id == category_id)
            .order_by(desc(Signal.computed_at))
            .limit(limit)
        )
        if signal_type:
            query = query.where(Signal.signal_type == signal_type)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_latest_by_type(
        self,
        signal_type: str,
        product_id: Optional[str] = None,
    ) -> Optional[Signal]:
        """Retorna o sinal mais recente de um tipo para um produto."""
        query = (
            select(Signal)
            .where(Signal.signal_type == signal_type)
            .order_by(desc(Signal.computed_at))
            .limit(1)
        )
        if product_id:
            query = query.where(Signal.product_id == product_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
