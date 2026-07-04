import json
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

def test_health_endpoint() -> None:
    """Verifies that the /health endpoint executes checks on DB/Redis and returns status."""
    mock_db = AsyncMock()
    mock_redis = AsyncMock()
    
    # Override FastAPI dependencies for database and redis
    from api.dependencies import get_redis_client
    from database.connection import get_db_session
    
    app.dependency_overrides[get_db_session] = lambda: mock_db
    app.dependency_overrides[get_redis_client] = lambda: mock_redis
    
    response = client.get("/health")
    
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["database"] == "connected"
    assert response.json()["redis"] == "connected"
    
    app.dependency_overrides.clear()

def test_monitor_product_endpoint() -> None:
    """Verifies that POST /products/{id} pushes a task into the Redis Stream and yields 202."""
    mock_redis = AsyncMock()
    
    from api.dependencies import get_redis_client
    app.dependency_overrides[get_redis_client] = lambda: mock_redis
    
    response = client.post("/products/MLB987654321")
    
    assert response.status_code == 202
    assert "queued" in response.json()["message"]
    
    # Confirm task was dispatched into Redis Stream
    mock_redis.xadd.assert_called_once()
    call_args = mock_redis.xadd.call_args
    stream_name = call_args[0][0]
    task_dict = call_args[0][1]
    
    assert stream_name == "ecommerce_tasks"
    assert task_dict["task_type"] == "crawl_product"
    
    payload = json.loads(task_dict["payload"])
    assert payload["product_id"] == "MLB987654321"
    
    app.dependency_overrides.clear()
