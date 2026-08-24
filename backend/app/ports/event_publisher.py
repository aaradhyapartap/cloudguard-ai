"""Port: domain event publication.

Publishing through a port rather than calling EventBridge inline means a service
can be unit-tested by asserting on what it *published*, without a bus. The
in-memory adapter records events in a list; the test reads the list.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.ai import DomainEvent


class EventPublisher(ABC):
    @abstractmethod
    async def publish(self, event: DomainEvent) -> None:
        """Publish a single event."""

    @abstractmethod
    async def publish_batch(self, events: list[DomainEvent]) -> None:
        """Publish several. Implementations should respect provider batch limits."""
