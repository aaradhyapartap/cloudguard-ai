"""Conditional worker slots; no reads or aggregate increment/decrement drift."""

from abc import ABC, abstractmethod


class WorkLeaseStore(ABC):
    @abstractmethod
    async def try_acquire(
        self, *, key: str, owner: str, now: int, expires_at: int,
    ) -> bool:
        """Acquire an absent/expired slot atomically; failure must not admit work."""

    @abstractmethod
    async def release(self, *, key: str, owner: str) -> bool:
        """Delete only a slot still owned by this acquisition; stale releases are false."""
