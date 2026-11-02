"""Bounded process-local leases for offline development and deterministic tests."""

import threading

from app.ports.work_lease_store import WorkLeaseStore


class InMemoryWorkLeaseStore(WorkLeaseStore):
    def __init__(self, *, max_keys: int = 10_000) -> None:
        if max_keys < 1:
            raise ValueError("The lease capacity must be positive.")
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._slots: dict[str, tuple[str, int]] = {}

    async def try_acquire(
        self, *, key: str, owner: str, now: int, expires_at: int,
    ) -> bool:
        if expires_at <= now:
            raise ValueError("A lease expiry must be in the future.")
        with self._lock:
            existing = self._slots.get(key)
            if existing is not None and existing[1] > now:
                return False
            if existing is None and len(self._slots) >= self._max_keys:
                self._slots = {
                    name: value for name, value in self._slots.items() if value[1] > now
                }
                if len(self._slots) >= self._max_keys:
                    return False
            self._slots[key] = (owner, expires_at)
            return True

    async def release(self, *, key: str, owner: str) -> bool:
        with self._lock:
            existing = self._slots.get(key)
            if existing is None or existing[0] != owner:
                return False
            del self._slots[key]
            return True
