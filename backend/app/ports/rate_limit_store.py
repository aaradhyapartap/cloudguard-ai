"""Atomic per-key request admission; provider failures must never admit work."""

from abc import ABC, abstractmethod


class RateLimitStore(ABC):
    @abstractmethod
    async def consume(self, *, key: str, limit: int, expires_at: int) -> bool:
        """Atomically admit at most limit requests for this window-specific key.

        False means quota exhausted. Dependency failure raises UpstreamError.
        expires_at is cleanup metadata, not the authority for window selection.
        """
