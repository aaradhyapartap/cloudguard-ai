"""EventBridge event publisher.

Implements :class:`app.ports.event_publisher.EventPublisher` against Amazon
EventBridge, and against LocalStack when ``AWS_ENDPOINT_URL`` is set.

**Publishing is best-effort, deliberately.** A failure to emit
``DocumentUploadRegistered`` must not fail the registration that already
succeeded — the row is written and the presigned URL is issued, and losing a
notification is a strictly smaller harm than losing the work. Failures are
logged at error level so they are visible rather than silent, and
``FailedEntryCount`` is inspected because ``put_events`` returns HTTP 200 even
when individual entries were rejected.

That is a deliberate departure from the usual "surface provider failure as an
application error" convention, and it is scoped to this adapter only: an event
bus is a side channel, not a system of record. If an event ever becomes
load-bearing — a Step Functions trigger that must fire — the correct answer is
an outbox table, not raising here.

**Batching.** ``put_events`` accepts at most 10 entries per call, so batches are
chunked. Exceeding it is a hard API error, not a soft truncation.
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any

from app.core.logging import get_logger
from app.models.ai import DomainEvent
from app.ports.event_publisher import EventPublisher

logger = get_logger(__name__)

# Hard EventBridge limit on PutEvents.
MAX_ENTRIES_PER_CALL = 10

# Stable conventions. Rules are written against these, so changing either is a
# breaking change for every subscriber — including future Step Functions
# triggers in Phase 11.
EVENT_SOURCE = "cloudguard.application"


class EventBridgePublisher(EventPublisher):
    def __init__(
        self,
        *,
        bus_name: str,
        region: str,
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        """``client`` is injectable so unit tests can stub boto3 entirely."""
        self._bus_name = bus_name

        if client is not None:
            self._client = client
            return

        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "events",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    def _entry(self, event: DomainEvent) -> dict[str, Any]:
        """Serialise a DomainEvent into an EventBridge entry.

        ``organization_id`` is promoted into the detail body rather than left
        implicit, so a rule can filter on tenant. An event without a tenant
        cannot be routed or scoped safely by any subscriber.
        """
        detail: dict[str, Any] = {
            "organization_id": event.organization_id,
            "correlation_id": event.correlation_id,
            "occurred_at": event.occurred_at.isoformat(),
            **event.payload,
        }
        return {
            "EventBusName": self._bus_name,
            "Source": EVENT_SOURCE,
            "DetailType": event.event_type,
            # default=str so a UUID or datetime that slipped into a payload
            # degrades to a string rather than raising mid-publish and losing
            # the whole batch.
            "Detail": json.dumps(detail, default=str),
        }

    async def publish(self, event: DomainEvent) -> None:
        await self.publish_batch([event])

    async def publish_batch(self, events: list[DomainEvent]) -> None:
        if not events:
            return

        for start in range(0, len(events), MAX_ENTRIES_PER_CALL):
            chunk = events[start : start + MAX_ENTRIES_PER_CALL]
            entries = [self._entry(event) for event in chunk]

            try:
                response = await asyncio.to_thread(
                    partial(self._client.put_events, Entries=entries)
                )
            except Exception as exc:
                # Deliberately not re-raised — see the module docstring.
                logger.error(
                    "event_publish_failed",
                    error=str(exc),
                    event_types=[event.event_type for event in chunk],
                    count=len(chunk),
                )
                continue

            failed = int(response.get("FailedEntryCount", 0) or 0)
            if failed:
                # PutEvents returns 200 with per-entry failures. Without this
                # check a partial failure is completely invisible.
                reasons = [
                    entry.get("ErrorCode", "unknown")
                    for entry in response.get("Entries", [])
                    if entry.get("ErrorCode")
                ]
                logger.error(
                    "event_publish_partial_failure",
                    failed=failed,
                    total=len(chunk),
                    error_codes=reasons,
                )
            else:
                logger.info(
                    "domain_events_published",
                    count=len(chunk),
                    transport="eventbridge",
                    event_types=[event.event_type for event in chunk],
                )
