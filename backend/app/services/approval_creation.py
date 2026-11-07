"""Trusted creation boundary for workflow-generated pending Approvals."""

from __future__ import annotations

from uuid import UUID

from app.models.agent_workflow import AgentWorkflowResearchResult
from app.models.approval import PendingApproval
from app.models.principal import Principal
from app.models.reviewer_agent import ReviewDecision, ReviewerAgentResult
from app.ports.approval_creation_repository import ApprovalCreationRepository
from app.services.workflow_recommendation import WorkflowRecommendationBuilder


class ApprovalCreationService:
    """Create one immutable pending Approval after a trusted Reviewer PASS."""

    def __init__(self, *, repository: ApprovalCreationRepository) -> None:
        self._repository = repository

    async def create_from_workflow(
        self,
        *,
        execution_id: str,
        principal: Principal,
        research: AgentWorkflowResearchResult,
        reviewer: ReviewerAgentResult,
        task_token: str,
    ) -> PendingApproval:
        """Persist the exact recommendation and internal Step Functions token."""
        if reviewer.decision is not ReviewDecision.PASS:
            raise ValueError("Reviewer FAIL must not create a pending Approval.")

        try:
            execution_uuid = UUID(execution_id)
        except ValueError as exc:
            raise ValueError(
                "Approval workflow execution_id must be a valid UUID."
            ) from exc

        recommendation = WorkflowRecommendationBuilder.build(
            execution_id=execution_uuid,
            research=research,
        )

        return await self._repository.create_pending(
            organization_id=principal.organization_id,
            workflow_execution_id=execution_id,
            recommendation_id=recommendation.recommendation_id,
            proposed_action=recommendation.proposed_action,
            evidence=recommendation.evidence,
            score_context=recommendation.score_context,
            agent_trace_ids=(),
            generator_model_id=recommendation.generator_model_id,
            reviewer_model_id=reviewer.model_id,
            task_token=task_token,
        )

