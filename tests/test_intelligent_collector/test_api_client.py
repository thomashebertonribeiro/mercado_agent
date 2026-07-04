"""
tests/test_intelligent_collector/test_api_client.py

Testes unitários e de propriedades do MercadoLivreAPICollector.

Feature: intelligent-collector
Tests: 20.1 – 20.5
"""

import httpx
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from collector.api_client import (
    MercadoLivreAPICollector,
    compute_backoff_wait,
    parse_retry_after,
)


# ─────────────────────────────────────────────────────────────────────────────
# 20.1 — Property: back-off formula is exact
# Property 10: Back-off formula is exact
# ─────────────────────────────────────────────────────────────────────────────

@given(
    attempt=st.integers(min_value=1, max_value=10),
    base=st.floats(min_value=1.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    max_wait=st.floats(min_value=1.0, max_value=300.0, allow_nan=False, allow_infinity=False),
)
@h_settings(max_examples=50)
def test_backoff_formula_is_exact(attempt: int, base: float, max_wait: float) -> None:
    """
    Property 10: Back-off formula is exact.
    Validates: Requirements 3.2
    """
    calculated = compute_backoff_wait(attempt, base, max_wait)
    expected = min(base ** attempt, max_wait)
    assert calculated == expected


# ─────────────────────────────────────────────────────────────────────────────
# 20.2 — Property: Retry-After integer parsing is exact
# Property 11: Retry-After integer parsing is exact
# ─────────────────────────────────────────────────────────────────────────────

@given(
    seconds=st.integers(min_value=0, max_value=3600)
)
@h_settings(max_examples=50)
def test_parse_retry_after_integer(seconds: int) -> None:
    """
    Property 11: Retry-After integer parsing is exact.
    Validates: Requirements 3.3
    """
    assert parse_retry_after(str(seconds)) == float(seconds)


# ─────────────────────────────────────────────────────────────────────────────
# 20.3 — Property: Retry-After HTTP-date parsing returns correct delta
# Property 12: Retry-After HTTP-date parsing returns correct delta
# ─────────────────────────────────────────────────────────────────────────────

@given(
    delta=st.timedeltas(
        min_value=timedelta(seconds=2),
        max_value=timedelta(hours=23)
    )
)
@h_settings(max_examples=30)
def test_parse_retry_after_http_date(delta: timedelta) -> None:
    """
    Property 12: Retry-After HTTP-date parsing returns correct delta.
    Validates: Requirements 3.4
    """
    now = datetime.now(timezone.utc)
    target_time = now + delta
    
    # Format target_time to HTTP-date format (e.g. "Wed, 21 Oct 2015 07:28:00 GMT")
    http_date = format_datetime(target_time)
    
    res = parse_retry_after(http_date, now=now)
    # Allow small tolerance since format_datetime might discard fractional seconds
    assert abs(res - delta.total_seconds()) <= 1.0

    # Past dates should return 0.0
    past_target = now - delta
    past_http_date = format_datetime(past_target)
    assert parse_retry_after(past_http_date, now=now) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 20.4 — HTTP 404 → no retry, raises immediately
# Validates: Requirements 3.6
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_404_no_retry_raises_immediately() -> None:
    """
    Verifica que um HTTP 404 interrompe as tentativas e lança HTTPStatusError imediatamente.
    Validates: Requirements 3.6
    """
    collector = MercadoLivreAPICollector()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=mock_response
    )

    with patch("httpx.AsyncClient.request", AsyncMock(return_value=mock_response)) as mock_request:
        with pytest.raises(httpx.HTTPStatusError):
            await collector._make_request("GET", "/items/MLB123")
        
        # O total de requisições deve ser 1 (nenhum retry para 404)
        assert mock_request.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# 20.5 — search_by_category returns None on 404
# Validates: Requirements 10.1
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_by_category_returns_none_on_404() -> None:
    """
    search_by_category deve retornar None e silenciar a exceção no caso de HTTP 404.
    Validates: Requirements 10.1
    """
    collector = MercadoLivreAPICollector()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=mock_response
    )

    with patch("httpx.AsyncClient.request", AsyncMock(return_value=mock_response)):
        res = await collector.search_by_category("MLB123", offset=0, limit=50)
        assert res is None
