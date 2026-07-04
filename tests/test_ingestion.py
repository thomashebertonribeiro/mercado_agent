"""
tests/test_ingestion.py

Testes para o IngestionService com schema event-sourced.

Contrato verificado:
  - Hash deduplication: payloads idênticos não geram eventos nem writes
  - PriceEvent criado quando preço ou estoque muda
  - ProductEvent criado quando status, título ou listing_type muda
  - Primeiro crawl sempre cria produto + evento de preço
  - Seller placeholder criado automaticamente quando produto referenciar seller inexistente
  - SellerEvent criado apenas quando reputação/nível muda
"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

from services.ingestion import IngestionService, _sha256, _price_event_hash, _product_dimension_hash
from models.events import PriceEvent, ProductEvent, SellerEvent


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_service() -> IngestionService:
    """Cria um IngestionService com todos os repositórios mockados."""
    session = AsyncMock()
    service = IngestionService(session)

    service.product_repo = AsyncMock()
    service.seller_repo = AsyncMock()
    service.raw_repo = MagicMock()   # add() é síncrono no RawPayload
    service.event_repo = AsyncMock()

    # Defaults seguros — podem ser sobrescritos por cada teste
    service.product_repo.get_by_id.return_value = None
    service.product_repo.get_current_hash.return_value = None
    service.seller_repo.get_by_id.return_value = None
    service.seller_repo.get_current_hash.return_value = None
    service.event_repo.get_last_hash_for_product.return_value = None
    service.event_repo.get_last_hash_for_seller.return_value = None
    service.event_repo.get_product_event_history.return_value = []
    service.event_repo.add = MagicMock()  # add() é síncrono

    return service


BASE_PRODUCT_PAYLOAD: dict = {
    "id": "MLB12345",
    "title": "Smartphone XYZ 128GB",
    "category_id": "MLB1055",
    "seller_id": 98765,
    "condition": "new",
    "listing_type_id": "gold_special",
    "price": 1999.90,
    "original_price": 2499.00,
    "currency_id": "BRL",
    "available_quantity": 50,
    "sold_quantity": 120,
    "status": "active",
    "permalink": "https://produto.mercadolivre.com.br/MLB12345",
}

BASE_SELLER_PAYLOAD: dict = {
    "id": 98765,
    "nickname": "MEGA_STORE",
    "site_id": "MLB",
    "points": 4500,
    "registration_date": "2018-03-10T10:00:00.000-03:00",
    "seller_reputation": {
        "level_id": "5_green",
        "power_seller_status": "platinum",
        "transactions": {"total": 8000, "canceled": 50, "completed": 7950},
        "metrics": {
            "positive": {"rate": 0.97, "value": 7760},
            "negative": {"rate": 0.01, "value": 80},
            "neutral":  {"rate": 0.02, "value": 160},
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Testes de produto — deduplicação
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_crawl_creates_product_and_price_event() -> None:
    """Primeiro crawl de um produto cria a dimensão e um PriceEvent."""
    service = _make_service()

    await service.ingest_product_payload(BASE_PRODUCT_PAYLOAD)

    # Produto deve ser adicionado (produto não existia)
    service.product_repo.add.assert_called_once()
    # Flush chamado para garantir FK antes dos eventos
    service.session.flush.assert_called()
    # Commit ao final
    service.session.commit.assert_called_once()
    # PriceEvent deve ser criado
    add_calls = service.event_repo.add.call_args_list
    event_types = [type(c.args[0]).__name__ for c in add_calls]
    assert "PriceEvent" in event_types


@pytest.mark.asyncio
async def test_no_event_when_price_hash_unchanged() -> None:
    """
    Nenhum PriceEvent criado se o hash de preço for idêntico ao último gravado.
    Verifica o contrato central de deduplicação por hash.
    """
    service = _make_service()

    # Produto já existe na dimensão
    existing_product = MagicMock()
    existing_product.id = "MLB12345"
    service.product_repo.get_by_id.return_value = existing_product

    # Hash da dimensão coincide com o payload atual
    dim_hash = _product_dimension_hash(
        "MLB12345",
        BASE_PRODUCT_PAYLOAD["title"],
        BASE_PRODUCT_PAYLOAD["category_id"],
        BASE_PRODUCT_PAYLOAD["seller_id"],
        BASE_PRODUCT_PAYLOAD["condition"],
        BASE_PRODUCT_PAYLOAD["listing_type_id"],
    )
    service.product_repo.get_current_hash.return_value = dim_hash

    # Hash de preço também coincide
    price_hash = _price_event_hash(
        "MLB12345",
        Decimal("1999.90"),
        Decimal("2499.00"),
        "BRL",
        50,
        120,
    )

    # Mock diferenciado por event_class: retorna price_hash para PriceEvent,
    # e None para ProductEvent (forçando geração de eventos estruturais —
    # mas o teste só valida que PriceEvent NÃO é criado).
    from models.events import PriceEvent as _PE
    async def _hash_by_class(event_class, product_id):
        if event_class is _PE:
            return price_hash
        return None
    service.event_repo.get_last_hash_for_product = _hash_by_class

    await service.ingest_product_payload(BASE_PRODUCT_PAYLOAD)

    # Nenhum PriceEvent deve ser adicionado (preço inalterado)
    price_event_calls = [
        c for c in service.event_repo.add.call_args_list
        if isinstance(c.args[0], PriceEvent)
    ]
    assert len(price_event_calls) == 0
    # Dimensão não deve ser atualizada (hash igual)
    service.product_repo.add.assert_not_called()


@pytest.mark.asyncio
async def test_price_event_created_on_price_change() -> None:
    """PriceEvent gerado quando o preço muda."""
    service = _make_service()

    # Produto existe
    existing_product = MagicMock()
    service.product_repo.get_by_id.return_value = existing_product

    # Hash da dimensão igual (título/categoria não mudou)
    dim_hash = _product_dimension_hash(
        "MLB12345",
        BASE_PRODUCT_PAYLOAD["title"],
        BASE_PRODUCT_PAYLOAD["category_id"],
        BASE_PRODUCT_PAYLOAD["seller_id"],
        BASE_PRODUCT_PAYLOAD["condition"],
        BASE_PRODUCT_PAYLOAD["listing_type_id"],
    )
    service.product_repo.get_current_hash.return_value = dim_hash

    # Hash de preço do PREÇO ANTERIOR (diferente do atual)
    old_price_hash = _price_event_hash(
        "MLB12345",
        Decimal("2200.00"),  # preço antigo
        Decimal("2499.00"),
        "BRL",
        50,
        120,
    )
    service.event_repo.get_last_hash_for_product.return_value = old_price_hash

    payload = {**BASE_PRODUCT_PAYLOAD, "price": 1999.90}  # novo preço
    await service.ingest_product_payload(payload)

    price_event_calls = [
        c for c in service.event_repo.add.call_args_list
        if isinstance(c.args[0], PriceEvent)
    ]
    assert len(price_event_calls) == 1
    event: PriceEvent = price_event_calls[0].args[0]
    assert event.price == Decimal("1999.90")
    assert event.available_qty == 50


@pytest.mark.asyncio
async def test_price_event_created_on_stock_change() -> None:
    """PriceEvent gerado quando o estoque muda, mesmo que o preço seja o mesmo."""
    service = _make_service()

    existing_product = MagicMock()
    service.product_repo.get_by_id.return_value = existing_product

    dim_hash = _product_dimension_hash(
        "MLB12345",
        BASE_PRODUCT_PAYLOAD["title"],
        BASE_PRODUCT_PAYLOAD["category_id"],
        BASE_PRODUCT_PAYLOAD["seller_id"],
        BASE_PRODUCT_PAYLOAD["condition"],
        BASE_PRODUCT_PAYLOAD["listing_type_id"],
    )
    service.product_repo.get_current_hash.return_value = dim_hash

    # Hash com estoque antigo (30 unidades)
    old_hash = _price_event_hash(
        "MLB12345",
        Decimal("1999.90"),
        Decimal("2499.00"),
        "BRL",
        30,   # estoque antigo
        120,
    )
    service.event_repo.get_last_hash_for_product.return_value = old_hash

    payload = {**BASE_PRODUCT_PAYLOAD, "available_quantity": 50}  # estoque novo
    await service.ingest_product_payload(payload)

    price_event_calls = [
        c for c in service.event_repo.add.call_args_list
        if isinstance(c.args[0], PriceEvent)
    ]
    assert len(price_event_calls) == 1
    assert price_event_calls[0].args[0].available_qty == 50


@pytest.mark.asyncio
async def test_product_event_created_on_status_change() -> None:
    """ProductEvent(status_change) gerado quando status muda de 'active' para 'paused'."""
    service = _make_service()

    existing_product = MagicMock()
    service.product_repo.get_by_id.return_value = existing_product
    service.product_repo.get_current_hash.return_value = "irrelevant_hash"  # dimensão sem mudança

    # Preço inalterado
    price_hash = _price_event_hash(
        "MLB12345", Decimal("1999.90"), Decimal("2499.00"), "BRL", 50, 120
    )
    service.event_repo.get_last_hash_for_product.return_value = price_hash

    # Último status era "active"
    mock_status_event = MagicMock()
    mock_status_event.new_value = "active"

    async def _mock_history(product_id, event_type=None, limit=1):
        if event_type == "status_change":
            return [mock_status_event]
        return []

    service.event_repo.get_product_event_history = _mock_history

    payload = {**BASE_PRODUCT_PAYLOAD, "status": "paused"}
    await service.ingest_product_payload(payload)

    product_event_calls = [
        c for c in service.event_repo.add.call_args_list
        if isinstance(c.args[0], ProductEvent)
    ]
    assert len(product_event_calls) >= 1
    status_events = [
        c for c in product_event_calls
        if c.args[0].event_type == "status_change"
    ]
    assert len(status_events) == 1
    assert status_events[0].args[0].new_value == "paused"
    assert status_events[0].args[0].old_value == "active"


@pytest.mark.asyncio
async def test_missing_product_id_aborts() -> None:
    """Payload sem 'id' não deve gerar nenhuma escrita."""
    service = _make_service()

    await service.ingest_product_payload({"title": "No ID Product", "price": 100})

    service.product_repo.add.assert_not_called()
    service.event_repo.add.assert_not_called()
    service.session.commit.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Testes de seller
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_seller_crawl_creates_dimension_and_event() -> None:
    """Primeiro crawl de vendedor cria dimensão e SellerEvent."""
    service = _make_service()

    await service.ingest_seller_payload(BASE_SELLER_PAYLOAD)

    service.seller_repo.add.assert_called_once()
    seller_event_calls = [
        c for c in service.event_repo.add.call_args_list
        if isinstance(c.args[0], SellerEvent)
    ]
    assert len(seller_event_calls) == 1
    service.session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_no_seller_event_when_reputation_unchanged() -> None:
    """Nenhum SellerEvent criado quando o hash de reputação é idêntico."""
    service = _make_service()

    existing_seller = MagicMock()
    service.seller_repo.get_by_id.return_value = existing_seller

    # Simula que o hash já foi gravado (reputação não mudou)
    # Calculamos o hash real para garantir que o teste é preciso
    from services.ingestion import _seller_event_hash
    existing_hash = _seller_event_hash(
        98765,
        "level_change",
        "platinum",
        Decimal("97.00"),
        Decimal("1.00"),
        Decimal("2.00"),
        8000,
        4500,
    )
    service.event_repo.get_last_hash_for_seller.return_value = existing_hash

    # Dimensão também inalterada
    from services.ingestion import _seller_dimension_hash
    dim_hash = _seller_dimension_hash(98765, "MEGA_STORE", "5_green", 4500, 8000)
    service.seller_repo.get_current_hash.return_value = dim_hash

    await service.ingest_seller_payload(BASE_SELLER_PAYLOAD)

    seller_event_calls = [
        c for c in service.event_repo.add.call_args_list
        if isinstance(c.args[0], SellerEvent)
    ]
    assert len(seller_event_calls) == 0


@pytest.mark.asyncio
async def test_seller_event_created_on_level_change() -> None:
    """SellerEvent criado quando o nível muda."""
    service = _make_service()

    existing_seller = MagicMock()
    service.seller_repo.get_by_id.return_value = existing_seller

    # Hash antigo com level_id diferente
    from services.ingestion import _seller_event_hash
    old_hash = _seller_event_hash(
        98765,
        "level_change",
        "gold",           # nível antigo
        Decimal("90.00"),
        Decimal("5.00"),
        Decimal("5.00"),
        5000,
        3000,
    )
    service.event_repo.get_last_hash_for_seller.return_value = old_hash

    # Dimensão com level antigo
    from services.ingestion import _seller_dimension_hash
    old_dim_hash = _seller_dimension_hash(98765, "MEGA_STORE", "4_light_green", 3000, 5000)
    service.seller_repo.get_current_hash.return_value = old_dim_hash

    await service.ingest_seller_payload(BASE_SELLER_PAYLOAD)  # agora é 5_green

    seller_event_calls = [
        c for c in service.event_repo.add.call_args_list
        if isinstance(c.args[0], SellerEvent)
    ]
    assert len(seller_event_calls) == 1
    assert seller_event_calls[0].args[0].event_type == "level_change"


@pytest.mark.asyncio
async def test_missing_seller_id_aborts() -> None:
    """Payload de seller sem 'id' não gera nenhuma escrita."""
    service = _make_service()

    await service.ingest_seller_payload({"nickname": "NoID"})

    service.seller_repo.add.assert_not_called()
    service.event_repo.add.assert_not_called()
    service.session.commit.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Testes de hash helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_sha256_deterministic() -> None:
    """O mesmo input sempre gera o mesmo hash."""
    h1 = _sha256("MLB12345", "active", "gold_special")
    h2 = _sha256("MLB12345", "active", "gold_special")
    assert h1 == h2
    assert len(h1) == 64


def test_sha256_different_inputs_different_hashes() -> None:
    """Inputs diferentes geram hashes diferentes."""
    h1 = _sha256("MLB12345", "active")
    h2 = _sha256("MLB12345", "paused")
    assert h1 != h2


def test_sha256_none_handling() -> None:
    """None é tratado como string vazia — não levanta exceção."""
    h = _sha256("MLB12345", None, "active", None)
    assert len(h) == 64
