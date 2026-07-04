from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from config.settings import settings
from api.routers import health, products, sellers, mercadolivre, intelligence, opportunity, analyst
from utils.logger import logger
from database.connection import ping_database

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
    title="E-Commerce Intelligence Data Lake API",
    description="REST API to manage monitoring targets and fetch historical metrics for Mercado Livre products.",
    version="1.0.0",
    lifespan=lifespan
)

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

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on port {settings.API_PORT} in {settings.ENV} mode.")
    uvicorn.run("main:app", host="0.0.0.0", port=settings.API_PORT, reload=(settings.ENV == "development"))
