import asyncio
import json
import signal
from typing import Any, Dict
import redis.asyncio as aioredis
from config.settings import settings
from database.connection import AsyncSessionLocal
from services.ingestion import IngestionService
from collector.api_client import MercadoLivreAPICollector
from utils.logger import logger

STREAM_NAME = "ecommerce_tasks"
GROUP_NAME = "datalake_workers"
CONSUMER_NAME = "worker_1"

async def initialize_redis_stream(redis_client: aioredis.Redis) -> None:
    """Configures the consumer group on Redis Stream if not already initialized."""
    try:
        await redis_client.xgroup_create(
            name=STREAM_NAME,
            groupname=GROUP_NAME,
            id="0",
            mkstream=True
        )
        logger.info(f"Redis Stream Group '{GROUP_NAME}' created on stream '{STREAM_NAME}'")
    except aioredis.exceptions.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.debug(f"Consumer group '{GROUP_NAME}' already exists.")
        else:
            logger.error(f"Failed to create consumer group: {e}")
            raise

async def process_task(task_type: str, payload: Dict[str, Any], collector: MercadoLivreAPICollector) -> None:
    """Routes the task to the correct service using a fresh database session context."""
    async with AsyncSessionLocal() as session:
        ingestion_service = IngestionService(session)
        
        if task_type == "crawl_product":
            product_id = payload.get("product_id")
            if not product_id:
                logger.error("Missing product_id in crawl_product payload")
                return
                
            # Call official API
            product_data = await collector.fetch_product(product_id)
            if product_data:
                await ingestion_service.ingest_product_payload(product_data)
                logger.info(f"Successfully processed product crawl for {product_id}")
            else:
                logger.warning(f"No product data returned for product {product_id}")
                
        elif task_type == "crawl_seller":
            seller_id = payload.get("seller_id")
            if not seller_id:
                logger.error("Missing seller_id in crawl_seller payload")
                return
                
            # Call official API
            seller_data = await collector.fetch_seller(int(seller_id))
            if seller_data:
                await ingestion_service.ingest_seller_payload(seller_data)
                logger.info(f"Successfully processed seller crawl for {seller_id}")
            else:
                logger.warning(f"No seller data returned for seller {seller_id}")
                
        else:
            logger.warning(f"Unknown task type received: {task_type}")

async def run_worker() -> None:
    """Worker main execution loop reading from Redis Streams."""
    logger.info("Starting Redis Stream Worker...")
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await initialize_redis_stream(redis_client)
    
    collector = MercadoLivreAPICollector()
    
    logger.info(f"Worker listening for tasks on stream '{STREAM_NAME}'...")

    shutdown_event = asyncio.Event()

    def _handle_sigterm() -> None:
        logger.info("SIGTERM recebido — drenando tarefas em andamento...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_sigterm)
    
    while not shutdown_event.is_set():
        try:
            # Block to wait for new task in stream
            streams = await redis_client.xreadgroup(
                groupname=GROUP_NAME,
                consumername=CONSUMER_NAME,
                streams={STREAM_NAME: ">"},
                count=1,
                block=5000
            )
            
            if not streams:
                if shutdown_event.is_set():
                    break
                await asyncio.sleep(0.1)
                continue
                
            for stream_name, messages in streams:
                for message_id, message_data in messages:
                    if shutdown_event.is_set():
                        break

                    task_type = message_data.get("task_type")
                    payload_str = message_data.get("payload", "{}")
                    
                    try:
                        payload = json.loads(payload_str)
                        logger.info(f"Processing task {task_type} (ID: {message_id})")
                        
                        await process_task(task_type, payload, collector)
                        
                        # Acknowledge task is complete
                        await redis_client.xack(STREAM_NAME, GROUP_NAME, message_id)
                        logger.debug(f"Acknowledged task {message_id}")
                        
                    except Exception as e:
                        logger.error(f"Failed to process task {message_id} of type {task_type}: {e}")
                        # Acknowledge failures to prevent blocking the stream (PEL growth)
                        await redis_client.xack(STREAM_NAME, GROUP_NAME, message_id)

        except asyncio.CancelledError:
            logger.info("Worker shutdown requested.")
            break
        except Exception as e:
            logger.error(f"Error in worker loop: {e}")
            await asyncio.sleep(5)

    # Drain complete — fechar conexões
    await redis_client.close()
    logger.info("Worker finalizado.")

if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker terminated by user.")
