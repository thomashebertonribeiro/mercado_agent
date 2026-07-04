"""
repositories/seller.py

Repositório para a tabela dimensão `sellers`.

Responsabilidades:
  1. Upsert do estado atual do vendedor
  2. Hash deduplication — busca apenas o hash atual para evitar writes desnecessários
  3. Consulta de último SellerEvent para comparação de reputação/nível
"""

from typing import Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models.seller import Seller
from models.events import SellerEvent
from repositories.base import BaseRepository


class SellerRepository(BaseRepository[Seller]):
    """Acesso à tabela dimensão `sellers`."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Seller, session)

    async def get_by_id(self, seller_id: int) -> Optional[Seller]:
        """Retorna o vendedor pelo id ML ou None."""
        return await self.session.get(Seller, seller_id)

    async def get_current_hash(self, seller_id: int) -> Optional[str]:
        """
        Retorna apenas o hash SHA-256 atual do vendedor.

        Permite ao serviço de ingestão detectar mudanças em O(1) sem
        carregar a entidade inteira — consulta de coluna única.
        """
        result = await self.session.execute(
            select(Seller.hash).where(Seller.id == seller_id)
        )
        return result.scalar_one_or_none()

    async def get_latest_event(self, seller_id: int) -> Optional[SellerEvent]:
        """
        Retorna o SellerEvent mais recente para um vendedor.

        Usado para comparar estado de reputação/nível antes de decidir
        se um novo evento precisa ser gerado.
        """
        result = await self.session.execute(
            select(SellerEvent)
            .where(SellerEvent.seller_id == seller_id)
            .order_by(desc(SellerEvent.occurred_at))
            .limit(1)
        )
        return result.scalar_one_or_none()
