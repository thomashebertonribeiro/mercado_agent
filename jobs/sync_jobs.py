"""
jobs/sync_jobs.py

Jobs de sincronizacao periodica.

Frequencias:
  - sync_seller_items:   a cada 1 hora
  - sync_competitors:    a cada 6 horas
  - sync_categories:     1x por dia
  - sync_trends:         1x por dia
  - sync_metrics:        1x por dia
"""

from datetime import datetime, timezone
from database.connection import AsyncSessionLocal
from services.sync_service import SyncService
from repositories.competitor import CompetitorRepository
from utils.logger import logger


async def sync_seller_items() -> None:
    """
    Sincroniza anuncios do vendedor autenticado.
    Busca lista de itens e atualiza dados no banco.
    """
    logger.info("Iniciando sync_seller_items")
    synced = 0
    errors = 0

    try:
        async with AsyncSessionLocal() as session:
            from repositories.marketplace_account import MarketplaceAccountRepository
            account_repo = MarketplaceAccountRepository(session)
            accounts = await account_repo.get_all("mercadolivre")

            for account in accounts:
                try:
                    sync = SyncService(session)
                    item_ids = await sync.fetch_seller_items(account.user_id)
                    items = await sync.fetch_items_batch(item_ids[:100], account.user_id)

                    for item in items:
                        try:
                            from services.ingestion import IngestionService
                            ingestion = IngestionService(session)
                            await ingestion.ingest_product_payload(item)
                            synced += 1
                        except Exception as e:
                            logger.warning(f"Erro ao ingerir item {item.get('id')}: {e}")
                            errors += 1
                except Exception as e:
                    logger.error(f"Erro ao sincronizar conta {account.user_id}: {e}")
                    errors += 1

        logger.info(f"sync_seller_items concluido: {synced} sincronizados, {errors} erros")
    except Exception as e:
        logger.error(f"sync_seller_items falhou: {e}")


async def sync_competitors() -> None:
    """
    Monitora concorrentes cadastrados.
    Verifica alteracoes de preco, estoque e status.
    """
    logger.info("Iniciando sync_competitors")
    monitored = 0
    changes_total = 0

    try:
        async with AsyncSessionLocal() as session:
            comp_repo = CompetitorRepository(session)
            competitors = await comp_repo.get_active_competitors()

            from repositories.marketplace_account import MarketplaceAccountRepository
            account_repo = MarketplaceAccountRepository(session)
            accounts = await account_repo.get_all("mercadolivre")
            user_id = accounts[0].user_id if accounts else None

            if not user_id:
                logger.warning("Nenhuma conta ML conectada para monitorar concorrentes")
                return

            sync = SyncService(session)
            for comp in competitors:
                try:
                    result = await sync.monitor_competitor(comp.ml_item_id, user_id)
                    monitored += 1
                    changes = result.get("changes", 0)
                    if changes > 0:
                        changes_total += changes
                        logger.info(
                            f"Concorrente {comp.ml_item_id}: {changes} alteracoes detectadas"
                        )
                except Exception as e:
                    logger.warning(f"Erro ao monitorar {comp.ml_item_id}: {e}")

        logger.info(f"sync_competitors concluido: {monitored} monitorados, {changes_total} alteracoes")
    except Exception as e:
        logger.error(f"sync_competitors falhou: {e}")


async def sync_categories() -> None:
    """
    Atualiza catalogo de categorias do ML.
    """
    logger.info("Iniciando sync_categories")
    try:
        async with AsyncSessionLocal() as session:
            sync = SyncService(session)
            categories = await sync.fetch_categories()
            logger.info(f"sync_categories concluido: {len(categories)} categorias raiz")
    except Exception as e:
        logger.error(f"sync_categories falhou: {e}")


async def sync_trends() -> None:
    """
    Coleta tendencias de mercado do ML.
    """
    logger.info("Iniciando sync_trends")
    try:
        async with AsyncSessionLocal() as session:
            sync = SyncService(session)
            trends = await sync.fetch_trends()
            logger.info(f"sync_trends concluido: {len(trends)} tendencias")
    except Exception as e:
        logger.error(f"sync_trends falhou: {e}")


async def sync_metrics() -> None:
    """
    Coleta metricas dos vendedores conectados.
    """
    logger.info("Iniciando sync_metrics")
    try:
        async with AsyncSessionLocal() as session:
            from repositories.marketplace_account import MarketplaceAccountRepository
            account_repo = MarketplaceAccountRepository(session)
            accounts = await account_repo.get_all("mercadolivre")

            sync = SyncService(session)
            for account in accounts:
                try:
                    metrics = await sync.fetch_seller_metrics(account.user_id)
                    if metrics:
                        logger.info(
                            f"Metricas {account.nickname}: "
                            f"nivel={metrics.get('level_id')}, "
                            f"vendas={metrics.get('transactions_total')}"
                        )
                except Exception as e:
                    logger.warning(f"Erro ao buscar metricas {account.user_id}: {e}")

        logger.info("sync_metrics concluido")
    except Exception as e:
        logger.error(f"sync_metrics falhou: {e}")
