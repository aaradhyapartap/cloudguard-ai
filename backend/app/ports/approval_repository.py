"""Port: durable persistence operations for human approvals."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.models.approval import (
    ApprovalAction,
    ApprovalEvidenceReference,
    ApprovalScoreContext,
    DecidedApproval,
    PendingApproval,
)
from app.models.enums import ApprovalDecision


@dataclass(frozen=True, slots=True)
class ApprovalCallbackContext:
    """Internal callback state.

    This type contains the secret Step Functions task token and must never be
    used as an API response or structured-log payload.
    """

    approval_id: UUID
    organization_id: UUID
    task_token: str
    decision: ApprovalDecision
    effective_action: ApprovalAction | None


class ApprovalRepository(ABC):
    """Persistence boundary for tenant-scoped human approval state."""

    @abstractmethod
    async def create_pending(
        self,
        *,
        organization_id: UUID,
        workflow_execution_id: str,
        recommendation_id: UUID,
        proposed_action: ApprovalAction,
        evidence: tuple[ApprovalEvidenceReference, ...],
        score_context: ApprovalScoreContext | None,
        agent_trace_ids: tuple[str, ...],
        generator_model_id: str | None,
        reviewer_model_id: str | None,
        task_token: str,
    ) -> PendingApproval:
        """Create one pending Approval and its internal task-token association."""

    @abstractmethod
    async def get_by_id(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
    ) -> PendingApproval | DecidedApproval | None:
        """Fetch one tenant-scoped Approval without exposing its task token."""

    @abstractmethod
    async def list_pending(
        self,
        *,
        organization_id: UUID,
        limit: int = 25,
    ) -> list[PendingApproval]:
        """List oldest pending Approvals for one tenant."""

    @abstractmethod
    async def decide_pending(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        decision: ApprovalDecision,
        approver_id: UUID,
        decided_at: datetime,
        justification: str | None,
        comment: str | None,
        modified_action: ApprovalAction | None,
    ) -> DecidedApproval | None:
        """Atomically transition one pending Approval to its human decision.

        Returns None when the compare-and-set predicate does not match.
        """

    @abstractmethod
    async def get_decided_callback_context(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
    ) -> ApprovalCallbackContext | None:
        """Return internal callback state only after the human decision exists."""
