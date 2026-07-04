import sys
from pathlib import Path
from loguru import logger
from config.settings import settings

def setup_logger() -> None:
    """Configures Loguru handlers for stdout and file logging with rotation/retention."""
    # Remove default handler
    logger.remove()

    # Log format for stdout
    stdout_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    # Console output
    logger.add(sys.stdout, format=stdout_format, level=settings.LOG_LEVEL)

    # File output (with rotation and retention)
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "data_lake.log"

    # Configure rotative file logging
    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} - {message}",
        level=settings.LOG_LEVEL,
        rotation=settings.LOG_ROTATION,
        retention=settings.LOG_RETENTION,
        compression="zip",
        enqueue=True,  # thread-safe and async-safe
    )

# Execute logging configuration
setup_logger()
