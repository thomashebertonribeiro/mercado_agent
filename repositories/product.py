"""
repositories/product.py

Repositório para a tabela dimensão `products`.

Com event sourcing, o repositório de produto não lida mais com snapshots
de métricas (ProductMetricsHistory foi removida). Seu papel agora é:

  1. Upsert do estado atual do produto (dimensão)
  2. Hash deduplication — buscar o hash atual para evitar writes desnecessários
  3. Consultas de estado atual por id / seller_id
"""

from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.product import Product
from repositories.base import BaseRepository


class ProductRepository(BaseRepository[Product]):
    """Acesso à tabela dimensão `products`."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Product, session)

    async def get_by_id(self, product_id: str) -> Optional[Product]:
        """Retorna o produto pelo id ML ou None."""
        return await self.session.get(Product, product_id)

    async def get_current_hash(self, product_id: str) -> Optional[str]:
        """
        Retorna apenas o hash SHA-256 atual do produto, sem carregar a entidade inteira.

        Usado pelo serviço de ingestão para decidir se houve mudança antes de
        qualquer escrita — consulta leve que carrega apenas uma coluna.
        """
        result = await self.session.execute(
            select(Product.hash).where(Product.id == product_id)
        )
        return result.scalar_one_or_none()

    async def list_by_seller(self, seller_id: int) -> Sequence[Product]:
        """Lista todos os produtos de um vendedor."""
        result = await self.session.execute(
            select(Product).where(Product.seller_id == seller_id)
        )
        return result.scalars().all()
