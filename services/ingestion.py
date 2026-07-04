"""
services/ingestion.py

Serviço de ingestão de payloads do Mercado Livre — Event-Sourced.

Fluxo por payload de produto:
  1. Salva RawPayload (bronze layer)
  2. Upsert do Seller (dimensão) — com hash deduplication
  3. Upsert do Product (dimensão) — com hash deduplication
  4. Gera PriceEvent se preço/estoque mudou (hash deduplication)
  5. Gera ProductEvent para cada campo estrutural que mudou (status, título, etc.)

Fluxo por payload de vendedor:
  1. Salva RawPayload (bronze layer)
  2. Upsert do Seller (dimensão) — com hash deduplication
  3. Gera SellerEvent se reputação/nível mudou (hash deduplication)

Deduplicação por hash SHA-256:
  - Antes de qualquer escrita, calcula o hash do estado atual
  - Compara com o hash armazenado na dimensão (get_current_hash)
  - Se igual → sem escrita → sem evento → nenhum dado gravado
  - Se diferente → upsert dimensão → insere evento com o delta

Isso elimina comparações campo a campo espalhadas pelo código e garante
que só haja escrita quando houve mudança real.
"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.events import PriceEvent, ProductEvent, SellerEvent
from models.product import Product
from models.raw_payload import RawPayload
from models.seller import Seller
from repositories.event import EventRepository
from repositories.product import ProductRepository
from repositories.raw_payload import RawPayloadRepository
from repositories.seller import SellerRepository
from utils.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# Hash helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sha256(*parts: Any) -> str:
    """
    Calcula SHA-256 a partir de uma sequência de partes concatenadas.

    Cada parte é convertida para string e separada por "|" para evitar
    colisões entre campos adjacentes (ex: "ab" + "c" ≠ "a" + "bc").

    Decimals são normalizados (trailing zeros removidos) antes da conversão
    para garantir que Decimal('1999.90') e Decimal('1999.9') produzam o
    mesmo hash — ambos representam o mesmo valor numérico.
    """
    def _to_str(p: Any) -> str:
        if p is None:
            return ""
        if isinstance(p, Decimal):
            # normalize() remove zeros à direita: 1999.90 → 1999.9
            # Mas converte também infinitos/NaN — improváveis aqui, mas seguro
            return str(p.normalize())
        return str(p)

    raw = "|".join(_to_str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _product_dimension_hash(
    product_id: str,
    title: str,
    category_id: Optional[str],
    seller_id: Any,
    condition: Optional[str],
    listing_type_id: Optional[str],
) -> str:
    """Hash do estado da dimensão produto — campos raramente mutáveis."""
    return _sha256(product_id, title, category_id, seller_id, condition, listing_type_id)


def _price_event_hash(
    product_id: str,
    price: Decimal,
    original_price: Optional[Decimal],
    currency_id: str,
    available_qty: int,
    sold_qty: Optional[int],
) -> str:
    """Hash do estado de preço/estoque — detecta qualquer mudança numérica."""
    return _sha256(product_id, price, original_price, currency_id, available_qty, sold_qty)


def _seller_dimension_hash(
    seller_id: Any,
    nickname: str,
    level_id: Optional[str],
    points: Optional[int],
    transactions_total: Optional[int],
) -> str:
    """Hash do estado da dimensão vendedor."""
    return _sha256(seller_id, nickname, level_id, points, transactions_total)


def _seller_event_hash(
    seller_id: Any,
    event_type: str,
    reputation_level: Optional[str],
    positive_pct: Optional[Decimal],
    negative_pct: Optional[Decimal],
    neutral_pct: Optional[Decimal],
    transactions_total: Optional[int],
    points: Optional[int],
) -> str:
    """Hash do estado de reputação/nível do vendedor."""
    return _sha256(
        seller_id, event_type, reputation_level,
        positive_pct, negative_pct, neutral_pct,
        transactions_total, points,
    )


# ─────────────────────────────────────────────────────────────────────────────
# IngestionService
# ─────────────────────────────────────────────────────────────────────────────

class IngestionService:
    """
    Processa payloads raw do Mercado Livre e os converte em eventos imutáveis.

    Toda lógica de deduplicação é baseada em hash SHA-256 — sem comparações
    campo a campo fora desta classe.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.product_repo = ProductRepository(session)
        self.seller_repo = SellerRepository(session)
        self.raw_repo = RawPayloadRepository(session)
        self.event_repo = EventRepository(session)

    # ─────────────────────────────────────────────────────────────────────────
    # Produto
    # ─────────────────────────────────────────────────────────────────────────

    async def ingest_product_payload(self, product_data: Dict[str, Any]) -> int:
        """
        Processa um payload JSON de produto.

        Passos:
          1. Salva RawPayload
          2. Upsert do Seller placeholder (se necessário)
          3. Upsert do Product (dimensão) com hash deduplication
          4. Gera PriceEvent se preço/estoque mudou
          5. Gera ProductEvent para status, título ou listing_type alterados

        Returns:
            Número de eventos gerados (PriceEvent + ProductEvents).
        """
        now = datetime.now(timezone.utc)
        events_generated = 0

        product_id = product_data.get("id")
        if not product_id:
            logger.error("Product payload missing 'id'. Aborting ingestion.")
            return 0

        # 1. Bronze layer — salva payload bruto independente de tudo
        self.raw_repo.add(
            RawPayload(
                resource_type="product",
                resource_id=product_id,
                payload=product_data,
                captured_at=now,
            )
        )

        # 2. Seller placeholder (enriquecido quando o crawler de sellers rodar)
        seller_id = product_data.get("seller_id")
        if seller_id:
            await self._ensure_seller_placeholder(seller_id, now)

        # 3. Upsert Product (dimensão)
        title = product_data.get("title", "Unknown Product")
        category_id = product_data.get("category_id")
        condition = product_data.get("condition")
        listing_type_id = product_data.get("listing_type_id")
        permalink = product_data.get("permalink")

        dimension_hash = _product_dimension_hash(
            product_id, title, category_id, seller_id, condition, listing_type_id
        )

        stored_hash = await self.product_repo.get_current_hash(product_id)
        product = await self.product_repo.get_by_id(product_id)

        if product is None:
            # Primeiro crawl — cria a entrada na dimensão
            price_raw = product_data.get("price", 0)
            initial_price = Decimal(str(price_raw)) if price_raw else None
            initial_qty = product_data.get("available_quantity")

            product = Product(
                id=product_id,
                title=title,
                category_id=category_id,
                seller_id=seller_id,
                condition=condition,
                listing_type_id=listing_type_id,
                initial_price=initial_price,
                initial_quantity=int(initial_qty) if initial_qty is not None else None,
                permalink=permalink,
                source="api",
                hash=dimension_hash,
                created_at=now,
                updated_at=now,
            )
            await self.product_repo.add(product)
            logger.info(f"New product registered: {product_id}")
        elif dimension_hash != stored_hash:
            # Houve mudança estrutural — atualiza dimensão
            product.title = title
            product.category_id = category_id
            product.condition = condition
            product.listing_type_id = listing_type_id
            product.permalink = permalink
            product.hash = dimension_hash
            product.updated_at = now
            logger.debug(f"Product dimension updated: {product_id}")

        # Flush para garantir que o produto existe antes de inserir FKs
        await self.session.flush()

        # 4. PriceEvent — só se preço/estoque mudou
        if await self._maybe_create_price_event(product_id, product_data, now):
            events_generated += 1

        # 5. ProductEvent — para cada campo estrutural com mudança detectada
        events_generated += await self._maybe_create_product_events(product_id, product_data, now)

        await self.session.commit()
        logger.debug(f"Ingest committed for product {product_id} ({events_generated} events)")
        return events_generated

    async def _maybe_create_price_event(
        self,
        product_id: str,
        data: Dict[str, Any],
        now: datetime,
    ) -> bool:
        """Cria um PriceEvent apenas se o estado de preço/estoque mudou.
        Returns True se um evento foi criado."""
        price_raw = data.get("price", 0)
        price = Decimal(str(price_raw)) if price_raw else Decimal("0")

        orig_raw = data.get("original_price")
        original_price = Decimal(str(orig_raw)) if orig_raw is not None else None

        currency_id = data.get("currency_id", "BRL")
        available_qty = int(data.get("available_quantity", 0))
        sold_qty_raw = data.get("sold_quantity")
        sold_qty = int(sold_qty_raw) if sold_qty_raw is not None else None

        event_hash = _price_event_hash(
            product_id, price, original_price, currency_id, available_qty, sold_qty
        )

        last_hash = await self.event_repo.get_last_hash_for_product(PriceEvent, product_id)
        if last_hash == event_hash:
            logger.debug(f"Price unchanged for {product_id} — skipping PriceEvent")
            return False

        self.event_repo.add(
            PriceEvent(
                product_id=product_id,
                price=price,
                original_price=original_price,
                currency_id=currency_id,
                available_qty=available_qty,
                sold_qty=sold_qty,
                occurred_at=now,
                source="api",
                hash=event_hash,
                created_at=now,
                updated_at=now,
            )
        )
        logger.info(
            f"PriceEvent created for {product_id}: "
            f"price={price}, qty={available_qty}"
        )
        return True

    async def _maybe_create_product_events(
        self,
        product_id: str,
        data: Dict[str, Any],
        now: datetime,
    ) -> int:
        """
        Cria ProductEvents para campos estruturais que mudaram.

        Cada tipo de campo gera um evento independente com old_value/new_value,
        permitindo rastrear exatamente o que mudou e quando.

        Returns: número de ProductEvents criados.
        """
        created = 0
        for field, event_type in [
            ("status", "status_change"),
            ("title", "title_change"),
            ("listing_type_id", "listing_type_change"),
        ]:
            new_value = str(data.get(field, "")) if data.get(field) is not None else None
            if new_value is None:
                continue

            field_hash = _sha256(product_id, event_type, new_value)

            # Busca o último evento deste tipo específico para obter old_value
            history = await self.event_repo.get_product_event_history(
                product_id, event_type=event_type, limit=1
            )
            old_value = history[0].new_value if history else None

            # Só cria evento se o valor realmente mudou
            if old_value == new_value:
                continue

            self.event_repo.add(
                ProductEvent(
                    product_id=product_id,
                    event_type=event_type,
                    old_value=old_value,
                    new_value=new_value,
                    occurred_at=now,
                    source="api",
                    hash=field_hash,
                    created_at=now,
                    updated_at=now,
                )
            )
            created += 1
            logger.info(
                f"ProductEvent({event_type}) for {product_id}: "
                f"'{old_value}' → '{new_value}'"
            )
        return created

    # ─────────────────────────────────────────────────────────────────────────
    # Vendedor
    # ─────────────────────────────────────────────────────────────────────────

    async def ingest_seller_payload(self, seller_data: Dict[str, Any]) -> int:
        """
        Processa um payload JSON de vendedor.

        Passos:
          1. Salva RawPayload
          2. Upsert do Seller (dimensão) com hash deduplication
          3. Gera SellerEvent se reputação/nível mudou

        Returns: número de eventos gerados (0 ou 1).
        """
        now = datetime.now(timezone.utc)

        seller_id = seller_data.get("id")
        if not seller_id:
            logger.error("Seller payload missing 'id'. Aborting ingestion.")
            return 0

        # 1. Bronze layer
        self.raw_repo.add(
            RawPayload(
                resource_type="seller",
                resource_id=str(seller_id),
                payload=seller_data,
                captured_at=now,
            )
        )

        # 2. Upsert Seller (dimensão)
        nickname = seller_data.get("nickname", f"Seller_{seller_id}")
        site_id = seller_data.get("site_id")
        level_id = seller_data.get("seller_reputation", {}).get("level_id") if seller_data.get("seller_reputation") else None
        points = seller_data.get("points")
        transactions_total = (
            seller_data.get("seller_reputation", {}).get("transactions", {}).get("total")
            if seller_data.get("seller_reputation")
            else None
        )

        reg_date_str = seller_data.get("registration_date")
        registration_date = None
        if reg_date_str:
            try:
                registration_date = datetime.fromisoformat(
                    reg_date_str.replace("Z", "+00:00")
                )
            except ValueError:
                logger.warning(f"Could not parse registration_date: {reg_date_str}")

        dimension_hash = _seller_dimension_hash(
            seller_id, nickname, level_id, points, transactions_total
        )

        stored_hash = await self.seller_repo.get_current_hash(seller_id)
        seller = await self.seller_repo.get_by_id(seller_id)

        if seller is None:
            seller = Seller(
                id=seller_id,
                nickname=nickname,
                site_id=site_id,
                level_id=level_id,
                points=points,
                transactions_total=transactions_total,
                registration_date=registration_date,
                source="api",
                hash=dimension_hash,
                created_at=now,
                updated_at=now,
            )
            await self.seller_repo.add(seller)
            logger.info(f"New seller registered: {seller_id} ({nickname})")
        elif dimension_hash != stored_hash:
            seller.nickname = nickname
            seller.site_id = site_id
            seller.level_id = level_id
            seller.points = points
            seller.transactions_total = transactions_total
            if registration_date:
                seller.registration_date = registration_date
            seller.hash = dimension_hash
            seller.updated_at = now
            logger.debug(f"Seller dimension updated: {seller_id}")

        await self.session.flush()

        # 3. SellerEvent — só se reputação/nível mudou
        events = 1 if await self._maybe_create_seller_event(seller_id, seller_data, now) else 0

        await self.session.commit()
        logger.debug(f"Ingest committed for seller {seller_id} ({events} events)")
        return events

    async def _maybe_create_seller_event(
        self,
        seller_id: int,
        data: Dict[str, Any],
        now: datetime,
    ) -> bool:
        """Cria um SellerEvent apenas se reputação ou nível do vendedor mudou.
        Returns True se um evento foi criado."""
        reputation = data.get("seller_reputation", {}) or {}

        reputation_level = reputation.get("power_seller_status")
        level_id = reputation.get("level_id")
        transactions_total_raw = reputation.get("transactions", {})
        transactions_total = (
            transactions_total_raw.get("total") if isinstance(transactions_total_raw, dict) else None
        )
        points = data.get("points")

        metrics = reputation.get("metrics", {}) or {}
        positive_pct = (
            Decimal(str(metrics.get("positive", {}).get("rate", 0))) * 100
            if metrics.get("positive")
            else None
        )
        negative_pct = (
            Decimal(str(metrics.get("negative", {}).get("rate", 0))) * 100
            if metrics.get("negative")
            else None
        )
        neutral_pct = (
            Decimal(str(metrics.get("neutral", {}).get("rate", 0))) * 100
            if metrics.get("neutral")
            else None
        )

        # Determina o tipo de evento pelo que mudou
        event_type = "reputation_change" if reputation_level or positive_pct else "other"
        if level_id:
            event_type = "level_change"

        event_hash = _seller_event_hash(
            seller_id, event_type, reputation_level,
            positive_pct, negative_pct, neutral_pct,
            transactions_total, points,
        )

        last_hash = await self.event_repo.get_last_hash_for_seller(seller_id)
        if last_hash == event_hash:
            logger.debug(f"Seller reputation unchanged for {seller_id} — skipping SellerEvent")
            return False

        self.event_repo.add(
            SellerEvent(
                seller_id=seller_id,
                event_type=event_type,
                reputation_level=reputation_level,
                positive_pct=positive_pct,
                negative_pct=negative_pct,
                neutral_pct=neutral_pct,
                transactions_total=transactions_total,
                points=points,
                occurred_at=now,
                source="api",
                hash=event_hash,
                created_at=now,
                updated_at=now,
            )
        )
        logger.info(f"SellerEvent({event_type}) created for seller {seller_id}")
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers internos
    # ─────────────────────────────────────────────────────────────────────────

    async def _ensure_seller_placeholder(self, seller_id: int, now: datetime) -> None:
        """
        Garante que o seller existe na dimensão antes de qualquer FK de produto.

        Cria um placeholder mínimo se o seller ainda não foi crawlado.
        O enriquecimento acontece quando ingest_seller_payload for chamado.
        """
        existing = await self.seller_repo.get_by_id(seller_id)
        if existing is None:
            placeholder_hash = _seller_dimension_hash(
                seller_id, f"Seller_{seller_id}", None, None, None
            )
            await self.seller_repo.add(
                Seller(
                    id=seller_id,
                    nickname=f"Seller_{seller_id}",
                    source="api",
                    hash=placeholder_hash,
                    created_at=now,
                    updated_at=now,
                )
            )
            await self.session.flush()
            logger.debug(f"Seller placeholder created: {seller_id}")
