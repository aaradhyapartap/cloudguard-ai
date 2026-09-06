"""Deterministic construction of bounded Phase 7 workflow recommendations."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from app.models.agent_workflow import AgentWorkflowResearchResult
from app.models.approval import ApprovalAction, ApprovalEvidenceReference
from app.models.workflow_recommendation import WorkflowRecommendation


class WorkflowRecommendationBuilder:
    """Build an application-owned recommendation from trusted workflow state."""

    @staticmethod
    def build(
        *,
        execution_id: UUID,
        research: AgentWorkflowResearchResult,
    ) -> WorkflowRecommendation:
        evidence: list[ApprovalEvidenceReference] = []

        for item in research.evidence:
            try:
                chunk_id = UUID(item.chunk_id)
                document_id = UUID(item.document_id)
            except ValueError as exc:
                raise ValueError(
                    "Workflow recommendation requires UUID-backed trusted evidence."
                ) from exc

            evidence.append(
                ApprovalEvidenceReference(
                    chunk_id=chunk_id,
                    document_id=document_id,
                )
            )

        recommendation_id = uuid5(
            NAMESPACE_URL,
            f"cloudguard-ai:workflow-recommendation:{execution_id}",
        )

        return WorkflowRecommendation(
            recommendation_id=recommendation_id,
            proposed_action=ApprovalAction(
                action_type="create_security_review",
                resource_type="workflow_execution",
                resource_id=execution_id,
                summary=(
                    "Create a bounded security review for this workflow execution."
                ),
            ),
            evidence=tuple(evidence),
            score_context=None,
            generator_model_id=None,
        )
