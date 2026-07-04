"""
repositories/event.py

Repositório para as tabelas de eventos particionadas.

Todas as tabelas de eventos são IMUTÁVEIS após inserção — não há update/delete.
O repositório expõe apenas operações de escrita (add) e consultas de leitura
para deduplicação por hash.

Suporta:
  - PriceEvent
  - ProductEvent
  - SellerEvent
  - RankingEvent
  - ReviewEvent
  - QuestionEvent
"""

from typing import Optional, Sequence, Type, TypeVar

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models.events import (
    PriceEvent,
    ProductEvent,
    SellerEvent,
    RankingEvent,
    ReviewEvent,
    QuestionEvent,
)

# Tipo genérico para qualquer classe de evento
AnyEvent = TypeVar(
    "AnyEvent",
    PriceEvent,
    ProductEvent,
    SellerEvent,
    RankingEvent,
    ReviewEvent,
    QuestionEvent,
)


class EventRepository:
    """
    Repositório centralizado para todas as tabelas de eventos.

    Uso:
        repo = EventRepository(session)
        await repo.add(PriceEvent(...))
        last_hash = await repo.get_last_hash(PriceEvent, product_id="MLB123")
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, event: AnyEvent) -> AnyEvent:
        """
        Adiciona um evento à sessão atual (sem flush imediato).

        O evento só é persistido quando a sessão for commitada.
        Retorna o evento para facilitar encadeamento.
        """
        self.session.add(event)
        return event

    async def get_last_hash_for_product(
        self,
        event_class: Type[AnyEvent],
        product_id: str,
    ) -> Optional[str]:
        """
        Retorna o hash do evento mais recente de um produto numa tabela de eventos.

        Usado para deduplicação: se o hash do payload atual for igual ao último
        hash gravado, nenhum novo evento é inserido.

        Funciona para: PriceEvent, ProductEvent, RankingEvent, ReviewEvent, QuestionEvent.
        """
        result = await self.session.execute(
            select(event_class.hash)
            .where(event_class.product_id == product_id)
            .order_by(desc(event_class.occurred_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_last_hash_for_seller(
        self,
        seller_id: int,
    ) -> Optional[str]:
        """
        Retorna o hash do SellerEvent mais recente para um vendedor.

        Deduplicação de eventos de reputação/nível antes de qualquer INSERT.
        """
        result = await self.session.execute(
            select(SellerEvent.hash)
            .where(SellerEvent.seller_id == seller_id)
            .order_by(desc(SellerEvent.occurred_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_price_history(
        self,
        product_id: str,
        limit: int = 100,
    ) -> Sequence[PriceEvent]:
        """
        Histórico de preços de um produto em ordem cronológica (mais antigo → mais novo).
        """
        result = await self.session.execute(
            select(PriceEvent)
            .where(PriceEvent.product_id == product_id)
            .order_by(PriceEvent.occurred_at)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_latest_price(self, product_id: str) -> Optional[PriceEvent]:
        """Retorna o PriceEvent mais recente de um produto."""
        result = await self.session.execute(
            select(PriceEvent)
            .where(PriceEvent.product_id == product_id)
            .order_by(desc(PriceEvent.occurred_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_product_event_history(
        self,
        product_id: str,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> Sequence[ProductEvent]:
        """
        Histórico de eventos estruturais de um produto.

        Se event_type for fornecido, filtra por tipo (ex: "status_change").
        """
        query = (
            select(ProductEvent)
            .where(ProductEvent.product_id == product_id)
            .order_by(ProductEvent.occurred_at)
            .limit(limit)
        )
        if event_type:
            query = query.where(ProductEvent.event_type == event_type)

        result = await self.session.execute(query)
        return result.scalars().all()
