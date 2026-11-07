"""Tenant worker quotas and short-lived slots, never approval/action authority."""

import hashlib
import math
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from enum import StrEnum
from uuid import UUID, uuid4

from app.core.errors import RateLimitError, UpstreamError
from app.core.logging import get_logger
from app.ports.rate_limit_store import RateLimitStore
from app.ports.work_lease_store import WorkLeaseStore
from app.security.rate_limits import RatePolicy, enforce_rate_limit

logger = get_logger(__name__)
LEASE_SECONDS = 600  # Exceeds both the 120s model and 300s ingestion Lambda runtimes.


class WorkKind(StrEnum):
    MODEL = "model"
    INGESTION = "ingestion"


class WorkAdmission:
    def __init__(
        self, *, leases: WorkLeaseStore, rates: RateLimitStore,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._leases = leases
        self._rates = rates
        self._clock = clock

    @asynccontextmanager
    async def admit(self, *, organization_id: UUID, kind: WorkKind) -> AsyncIterator[None]:
        now = math.floor(self._clock())
        owner = uuid4().hex
        digest = hashlib.sha256(str(organization_id).encode()).hexdigest()
        slots = 2 if kind is WorkKind.MODEL else 1
        policy = RatePolicy(f"worker-{kind.value}", 60 if kind is WorkKind.MODEL else 10)
        acquired: str | None = None
        try:
            for index in range(slots):
                key = f"slot:v1:{kind.value}:{digest}:{index}"
                if await self._leases.try_acquire(
                    key=key, owner=owner, now=now, expires_at=now + LEASE_SECONDS,
                ):
                    acquired = key
                    break
        except Exception as exc:
            raise UpstreamError("Worker admission is temporarily unavailable.") from exc
        if acquired is None:
            raise RateLimitError(retry_after_seconds=60)

        try:
            await enforce_rate_limit(
                self._rates, policy=policy, subject=("tenant-work", str(organization_id)),
                namespace="worker", clock=self._clock,
            )
            yield
        finally:
            try:
                released = await self._leases.release(key=acquired, owner=owner)
                if not released:
                    logger.warning("worker_slot_release_stale", work_kind=kind.value)
            except Exception as exc:
                # Work may already have succeeded. Do not convert a cleanup
                # outage into a replay trigger; expiry recovers capacity later.
                logger.warning(
                    "worker_slot_cleanup_failed", work_kind=kind.value,
                    error_type=type(exc).__name__,
                )
