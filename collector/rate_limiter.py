import asyncio

from utils.logger import logger


class RateLimiter:
    """
    Manages two concerns:
    - A shared asyncio.Semaphore for concurrency control (max concurrent requests)
    - A global 429-triggered pause via an asyncio.Event

    Convention: _pause_event.set() means NOT paused; _pause_event.clear() means paused.
    In-flight requests already holding the semaphore are NOT affected by a pause —
    only new acquire() calls block at the pause_event.wait() step.
    """

    def __init__(self, max_concurrency: int = 5) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # set = not paused; clear = paused
        self._pause_lock = asyncio.Lock()

    async def acquire(self) -> None:
        """
        Waits for:
          1. The global pause to be lifted (pause_event.wait()) — blocks if 429 pause is active
          2. A semaphore slot to become available

        Both conditions must be met before a new request is dispatched.
        Already in-flight requests (holding the semaphore) are NOT affected
        when a 429 pause is triggered.
        """
        await self._pause_event.wait()
        await self._semaphore.acquire()

    def release(self) -> None:
        """Releases the semaphore slot acquired by acquire()."""
        self._semaphore.release()

    async def pause_for(self, seconds: float) -> None:
        """
        Clears the pause event (blocks all future acquire() calls),
        waits `seconds`, then sets the event again.

        Serialised via _pause_lock: concurrent calls do not stack sleep durations.
        The second caller waits for the first to finish, then sees the event
        already set and returns immediately.
        """
        async with self._pause_lock:
            if not self._pause_event.is_set():
                # Already paused — wait for the existing pause to finish
                await self._pause_event.wait()
                return

            # We are the first to trigger the pause
            self._pause_event.clear()
            logger.warning("Rate limiter paused", wait_seconds=seconds)
            await asyncio.sleep(seconds)
            self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        """Returns True if a 429 pause is currently active."""
        return not self._pause_event.is_set()
