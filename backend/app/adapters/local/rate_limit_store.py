"""Bounded process-local counters, permitted only for local development."""

import threading
import time
from collections.abc import Callable

from app.ports.rate_limit_store import RateLimitStore


class InMemoryRateLimitStore(RateLimitStore):
    def __init__(
        self, *, max_keys: int = 10_000, clock: Callable[[], float] = time.time,
    ) -> None:
        if max_keys < 1:
            raise ValueError("The counter capacity must be positive.")
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._counters: dict[str, tuple[int, int]] = {}

    async def consume(self, *, key: str, limit: int, expires_at: int) -> bool:
        with self._lock:
            now = self._clock()
            existing = self._counters.get(key)
            if existing is not None and existing[1] <= now:
                del self._counters[key]
                existing = None
            if existing is None and len(self._counters) >= self._max_keys:
                self._counters = {
                    name: value for name, value in self._counters.items() if value[1] > now
                }
                if len(self._counters) >= self._max_keys:
                    return False
            count = existing[0] if existing is not None else 0
            if limit < 1 or count >= limit or expires_at <= now:
                return False
            self._counters[key] = (count + 1, expires_at)
            return True
