"""Application service for human Approval review and decisions.

The HTTP layer is deliberately thin. Authorization, tenant derivation,
decision-state checks, and human identity binding live here so callers cannot
bypass them by invoking the service outside FastAPI.

Phase 7.3 does not invoke Step Functions. Workflow callback/resume belongs to
Phase 7.4 after the human decision has been durably persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import ConflictError, NotFoundError
from app.models.approval import (
    ApprovalDecisionRequest,
    DecidedApproval,
    PendingApproval,
)
from app.models.enums import ApprovalStatus
from app.models.principal import Principal
from app.ports.approval_repository import ApprovalRepository
from app.security.authz import Permission, require_permission


class ApprovalService:
    """Coordinates tenant-scoped human approval reads and decisions."""

    def __init__(self, *, repository: ApprovalRepository) -> None:
        self._repo = repository

    async def list_pending(
        self,
        *,
        principal: Principal,
        limit: int = 25,
    ) -> list[PendingApproval]:
        """Return the pending approval queue visible to the caller's tenant."""
        require_permission(principal, Permission.APPROVAL_READ)

        return await self._repo.list_pending(
            organization_id=principal.organization_id,
            limit=limit,
        )

    async def get_approval(
        self,
        *,
        principal: Principal,
        approval_id: UUID,
    ) -> PendingApproval | DecidedApproval:
        """Return one tenant-owned approval without exposing task-token state."""
        require_permission(principal, Permission.APPROVAL_READ)

        approval = await self._repo.get_by_id(
            organization_id=principal.organization_id,
            approval_id=approval_id,
        )
        if approval is None:
            raise NotFoundError("The requested approval does not exist.")

        return approval

    async def decide(
        self,
        *,
        principal: Principal,
        approval_id: UUID,
        request: ApprovalDecisionRequest,
    ) -> DecidedApproval:
        """Durably record the first authorized human decision.

        Tenant and approver identity come only from the verified Principal.
        The repository performs the final compare-and-set so concurrent
        decision attempts cannot overwrite the first human decision.
        """
        require_permission(principal, Permission.APPROVAL_DECIDE)

        current = await self._repo.get_by_id(
            organization_id=principal.organization_id,
            approval_id=approval_id,
        )
        if current is None:
            raise NotFoundError("The requested approval does not exist.")

        if current.status is not ApprovalStatus.PENDING:
            raise ConflictError("The approval has already been decided.")

        decided = await self._repo.decide_pending(
            organization_id=principal.organization_id,
            approval_id=approval_id,
            decision=request.decision,
            approver_id=principal.user_id,
            decided_at=datetime.now(UTC),
            justification=request.justification,
            comment=request.comment,
            modified_action=request.modified_action,
        )

        if decided is None:
            # Another Manager won the compare-and-set after our initial read.
            # Never retry a human decision automatically.
            raise ConflictError("The approval has already been decided.")

        return decided
