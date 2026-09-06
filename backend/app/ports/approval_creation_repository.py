"""Port for creating the immutable workflow Approval snapshot."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.models.approval import (
    ApprovalAction,
    ApprovalEvidenceReference,
    ApprovalScoreContext,
    PendingApproval,
)


class ApprovalCreationRepository(Protocol):
    """Minimal persistence boundary required by the workflow creation worker."""

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
        ...
