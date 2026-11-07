"""Port for durable post-decision Approval callback recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.models.approval import ApprovalAction
from app.models.enums import ApprovalDecision


@dataclass(frozen=True, slots=True)
class ApprovalCallbackClaim:
    """Internal leased callback state containing the secret task token."""

    claim_id: UUID
    approval_id: UUID
    organization_id: UUID
    task_token: str
    decision: ApprovalDecision
    effective_action: ApprovalAction | None


class ApprovalCallbackRecoveryRepository(Protocol):
    """Persistence boundary for scheduled callback recovery."""

    async def list_organization_ids(
        self,
        *,
        after_id: UUID | None = None,
        limit: int = 500,
    ) -> tuple[UUID, ...]:
        """Enumerate one ordered page of tenant ids."""

    async def list_recoverable_approval_ids(
        self,
        *,
        organization_id: UUID,
        stale_before: datetime,
        limit: int = 25,
    ) -> tuple[UUID, ...]:
        """List undecivered callback candidates inside one tenant."""

    async def claim_decided_callback(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        stale_before: datetime,
    ) -> ApprovalCallbackClaim | None:
        """Acquire or reclaim a stale callback lease."""

    async def mark_callback_delivered(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        claim_id: UUID,
    ) -> bool:
        """Mark delivery only when the caller still owns the lease."""

    async def release_callback_claim(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        claim_id: UUID,
    ) -> bool:
        """Release an unsuccessful lease without erasing attempt history."""
