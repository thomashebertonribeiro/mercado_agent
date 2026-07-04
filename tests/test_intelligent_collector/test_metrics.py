"""
tests/test_intelligent_collector/test_metrics.py

Testes unitários e de propriedades do MetricsCollector.

Feature: intelligent-collector
Tests: 19.1 – 19.4
"""

import time
import pytest
from unittest.mock import MagicMock, patch
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from collector.metrics import MetricsCollector


# ─────────────────────────────────────────────────────────────────────────────
# 19.1 — Property: avg_seconds_per_page equals arithmetic mean
# Property 14: avg_seconds_per_page equals arithmetic mean
# ─────────────────────────────────────────────────────────────────────────────

@given(
    durations=st.lists(
        st.floats(min_value=0.001, max_value=100.0, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=200,
    )
)
@h_settings(max_examples=50)
def test_avg_seconds_per_page_arithmetic_mean(durations: list[float]) -> None:
    """
    Property 14: avg_seconds_per_page equals arithmetic mean.
    Validates: Requirements 6.3, 10.6
    """
    metrics = MetricsCollector()
    for d in durations:
        metrics.record_page_duration(d)
    
    expected = sum(durations) / len(durations)
    assert pytest.approx(metrics.avg_seconds_per_page) == expected


# ─────────────────────────────────────────────────────────────────────────────
# 19.2 — avg_seconds_per_page is None when no pages recorded
# ─────────────────────────────────────────────────────────────────────────────

def test_avg_seconds_per_page_none_initially() -> None:
    """
    avg_seconds_per_page deve ser None se nenhuma página foi registrada.
    """
    metrics = MetricsCollector()
    assert metrics.avg_seconds_per_page is None


# ─────────────────────────────────────────────────────────────────────────────
# 19.3 — finish() sets duration_seconds and end memory
# Validates: Requirements 6.2, 6.4
# ─────────────────────────────────────────────────────────────────────────────

def test_finish_sets_duration_and_memory() -> None:
    """
    finish() deve preencher a duração e coletar o RSS de término.
    Validates: Requirements 6.2, 6.4
    """
    metrics = MetricsCollector()
    
    mock_process = MagicMock()
    mock_process.memory_info.return_value.rss = 104857600  # 100 MB
    
    with patch("psutil.Process", return_value=mock_process):
        metrics.start()
        # Mock time drift
        metrics._start_time = time.monotonic() - 5.5
        metrics.finish()

    assert metrics.duration_seconds == 5
    assert metrics.memory_rss_start_mb == 100.0
    assert metrics.memory_rss_end_mb == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# 19.4 — to_extra_metrics() always contains all three keys
# Validates: Requirements 6.7
# ─────────────────────────────────────────────────────────────────────────────

def test_to_extra_metrics_keys() -> None:
    """
    to_extra_metrics() deve sempre ter as 3 chaves solicitadas, mesmo com valores nulos.
    Validates: Requirements 6.7
    """
    metrics = MetricsCollector()
    extra = metrics.to_extra_metrics()
    
    assert "avg_seconds_per_page" in extra
    assert "memory_rss_start_mb" in extra
    assert "memory_rss_end_mb" in extra
    
    assert extra["avg_seconds_per_page"] is None
    assert extra["memory_rss_start_mb"] is None
    assert extra["memory_rss_end_mb"] is None
