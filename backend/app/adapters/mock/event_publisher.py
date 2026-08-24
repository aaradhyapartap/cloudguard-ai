"""In-memory event publisher that records what was published.

Tests assert on ``publisher.events`` instead of standing up a bus.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.ai import DomainEvent
from app.ports.event_publisher import EventPublisher

logger = get_logger(__name__)


class InMemoryEventPublisher(EventPublisher):
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)
        logger.info(
            "domain_event_published",
            event_type=event.event_type,
            transport="memory",
        )

    async def publish_batch(self, events: list[DomainEvent]) -> None:
        for event in events:
            await self.publish(event)
