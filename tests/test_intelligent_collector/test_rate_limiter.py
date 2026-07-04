"""
tests/test_intelligent_collector/test_rate_limiter.py

Testes unitários e de propriedades do RateLimiter.

Feature: intelligent-collector
Tests: 18.1 – 18.3
"""

import asyncio
from unittest.mock import patch
import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from collector.rate_limiter import RateLimiter


# ─────────────────────────────────────────────────────────────────────────────
# 18.1 — Property: concurrency cap is never exceeded
# Property 9: Concurrency cap is never exceeded
# ─────────────────────────────────────────────────────────────────────────────

@given(
    max_concurrency=st.integers(min_value=1, max_value=10),
    items_count=st.integers(min_value=11, max_value=40),
)
@h_settings(max_examples=10, deadline=None)
def test_concurrency_cap_never_exceeded(max_concurrency: int, items_count: int) -> None:
    """
    Property 9: Concurrency cap is never exceeded.
    Validates: Requirements 4.2, 4.3, 4.4
    """
    async def run_property_test():
        limiter = RateLimiter(max_concurrency=max_concurrency)
        in_flight = 0
        peak_concurrency = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal in_flight, peak_concurrency
            await limiter.acquire()
            async with lock:
                in_flight += 1
                if in_flight > peak_concurrency:
                    peak_concurrency = in_flight

            # Simulate short work
            await asyncio.sleep(0.005)

            async with lock:
                in_flight -= 1
            limiter.release()

        workers = [asyncio.create_task(worker()) for _ in range(items_count)]
        await asyncio.gather(*workers)

        assert peak_concurrency <= max_concurrency
        assert in_flight == 0

    asyncio.run(run_property_test())


# ─────────────────────────────────────────────────────────────────────────────
# 18.2 — 429 pause blocks new dispatches, in-flight requests complete
# Validates: Requirements 4.5
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_429_pause_blocks_new_dispatches() -> None:
    """
    Verifica que uma chamada a pause_for bloqueia novas chamadas de acquire(),
    mas permite que tarefas já iniciadas terminem (ou continuem a rodar).
    Validates: Requirements 4.5
    """
    limiter = RateLimiter(max_concurrency=2)
    released = []

    # Trigger a pause asynchronously
    pause_task = asyncio.create_task(limiter.pause_for(0.1))

    # A task trying to acquire should block during pause
    async def try_acquire():
        await limiter.acquire()
        released.append(True)
        limiter.release()

    third_task = asyncio.create_task(try_acquire())
    
    # Wait briefly to let things process
    await asyncio.sleep(0.02)
    assert limiter.is_paused is True
    # The task should NOT have acquired yet (blocked by pause)
    assert len(released) == 0

    # Wait for the pause to lift
    await pause_task
    await third_task

    # Now the task should have completed
    assert len(released) == 1
    assert limiter.is_paused is False


# ─────────────────────────────────────────────────────────────────────────────
# 18.3 — is_paused reflects pause state correctly
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_paused_reflects_correct_state() -> None:
    """
    Verifica que is_paused reflete o estado interno de pausa do evento.
    """
    limiter = RateLimiter(max_concurrency=5)
    assert limiter.is_paused is False

    pause_task = asyncio.create_task(limiter.pause_for(0.05))
    await asyncio.sleep(0.01)
    assert limiter.is_paused is True

    await pause_task
    assert limiter.is_paused is False
