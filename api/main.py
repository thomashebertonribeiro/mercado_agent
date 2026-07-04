import time

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from config.settings import settings
from api.routers import (
    health, products, sellers, mercadolivre,
    intelligence, opportunity, analyst,
    scanner_routes, discoveries, metrics,
    collection, dashboard, auth,
)
from utils.logger import logger
from database.connection import ping_database
from observability.metrics import platform_metrics

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup event: Validate systems check
    logger.info("Initializing REST API...")
    db_connected = await ping_database()
    if db_connected:
        logger.info("Database connection successful on startup check.")
    else:
        logger.error("Database connection failure during startup verification.")
    yield
    # Shutdown event
    logger.info("Shutting down REST API...")

app = FastAPI(
    title="Market Intelligence Platform API",
    description=(
        "Plataforma de Inteligência de Mercado para Marketplaces. "
        "Radar de Oportunidades — descobre produtos automaticamente, "
        "acompanha mudanças e gera recomendações priorizadas."
    ),
    version="2.0.0",
    lifespan=lifespan
)


# ── Observability middleware ─────────────────────────────────────────────
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    platform_metrics.requests_in_flight.inc()
    t0 = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - t0
    platform_metrics.requests_in_flight.dec()

    # Track by path pattern (strip dynamic segments)
    path = request.url.path
    for segment in request.path_params.values():
        path = path.replace(str(segment), "{id}", 1)
    logger.debug(
        "Request",
        method=request.method,
        path=path,
        status=response.status_code,
        elapsed=round(elapsed, 3),
    )
    return response

# Global custom error handler to hide details and log them with Loguru
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled error occurred: {exc} on path: {request.url.path}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred. Please contact the administrator."}
    )

# Register routes
app.include_router(health.router)
app.include_router(products.router)
app.include_router(sellers.router)
app.include_router(mercadolivre.router)
app.include_router(intelligence.router)
app.include_router(opportunity.router)
app.include_router(analyst.router)
app.include_router(scanner_routes.router)
app.include_router(discoveries.router)
app.include_router(metrics.router)
app.include_router(collection.router)
app.include_router(dashboard.router)
app.include_router(auth.router)

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on port {settings.API_PORT} in {settings.ENV} mode.")
    uvicorn.run("api.main:app", host="0.0.0.0", port=settings.API_PORT, reload=(settings.ENV == "development"))
