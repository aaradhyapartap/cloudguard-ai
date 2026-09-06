"""Post-commit recovery service for Approval workflow callbacks."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.ports.approval_callback import ApprovalCallbackPort
from app.ports.approval_callback_recovery_repository import (
    ApprovalCallbackRecoveryRepository,
)


class ApprovalCallbackService:
    """Resume durably decided Approvals through ownership-safe callback leases."""

    def __init__(
        self,
        *,
        repository: ApprovalCallbackRecoveryRepository,
        callback: ApprovalCallbackPort,
    ) -> None:
        self._repository = repository
        self._callback = callback

    async def resume_decided(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        stale_before: datetime,
    ) -> bool:
        """Attempt one callback delivery using a durable lease."""

        claim = await self._repository.claim_decided_callback(
            organization_id=organization_id,
            approval_id=approval_id,
            stale_before=stale_before,
        )

        if claim is None:
            return False

        try:
            await self._callback.send_decision(
                task_token=claim.task_token,
                decision=claim.decision,
                effective_action=claim.effective_action,
            )
        except Exception:
            await self._repository.release_callback_claim(
                organization_id=organization_id,
                approval_id=approval_id,
                claim_id=claim.claim_id,
            )
            raise

        delivered = await self._repository.mark_callback_delivered(
            organization_id=organization_id,
            approval_id=approval_id,
            claim_id=claim.claim_id,
        )

        if not delivered:
            raise RuntimeError(
                "Approval callback succeeded but its delivery marker "
                "could not be persisted."
            )

        return True
