import asyncio
from datetime import datetime, timezone
import time
from typing import Any, Callable, Coroutine, Dict, Optional, Tuple, TypeVar

T = TypeVar("T")


class TTLCache:
    """In-memory bounded TTL cache conforming to SPEC-v1.3 §13.2."""

    # Default TTLs (SPEC §13.2)
    TTL_GEO = 7 * 24 * 3600       # 7 days
    TTL_CURRENT = 10 * 60         # 10 minutes
    TTL_HOURLY = 15 * 60          # 15 minutes
    TTL_DAILY = 30 * 60           # 30 minutes
    TTL_HISTORICAL = 24 * 3600    # 24 hours

    def __init__(self, max_size: int = 1000):
        self._max_size = max_size
        self._store: Dict[str, Tuple[Any, float]] = {}

    def get(self, key: str) -> Optional[Any]:
        """Retrieve unexpired value from cache."""
        item = self._store.get(key)
        if not item:
            return None
        val, expires_at = item
        if time.time() >= expires_at:
            self._store.pop(key, None)
            return None
        return val

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        """Store value with TTL; prune expired if size exceeds max_size."""
        now = time.time()
        if len(self._store) >= self._max_size:
            # Prune expired items
            expired_keys = [k for k, (_, exp) in self._store.items() if now >= exp]
            for k in expired_keys:
                self._store.pop(k, None)
            # If still over limit, pop oldest item
            if len(self._store) >= self._max_size:
                first_key = next(iter(self._store))
                self._store.pop(first_key, None)

        self._store[key] = (value, now + ttl_seconds)

    def clear(self) -> None:
        self._store.clear()


class SingleFlight:
    """Async single-flight deduplication for concurrent identical queries (SPEC §13.2, §16, C40)."""

    def __init__(self):
        self._in_flight: Dict[str, asyncio.Task] = {}

    async def execute(
        self,
        key: str,
        coro_fn: Callable[[], Coroutine[Any, Any, T]],
    ) -> T:
        """Execute coro_fn once for identical concurrent key, deduplicating inflight calls."""
        task = self._in_flight.get(key)
        if task is None:
            # Shared work has its own lifetime: cancelling any one HTTP caller
            # must not cancel or strand the other callers sharing its result.
            task = asyncio.create_task(coro_fn())
            self._in_flight[key] = task

            def finished(done):
                if self._in_flight.get(key) is done:
                    self._in_flight.pop(key, None)
                if not done.cancelled():
                    # Retrieve exceptions even when every caller has gone away.
                    done.exception()

            task.add_done_callback(finished)
        return await asyncio.shield(task)
