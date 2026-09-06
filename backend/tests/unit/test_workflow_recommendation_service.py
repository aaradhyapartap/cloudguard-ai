"""Unit tests for deterministic workflow recommendation construction."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.models.agent_workflow import (
    AgentWorkflowEvidence,
    AgentWorkflowResearchResult,
)
from app.models.ai import TokenUsage
from app.services.workflow_recommendation import WorkflowRecommendationBuilder

EXECUTION_ID = UUID("11111111-1111-4111-8111-111111111111")
CHUNK_ID = UUID("22222222-2222-4222-8222-222222222222")
DOCUMENT_ID = UUID("33333333-3333-4333-8333-333333333333")


def _research(
    *,
    chunk_id: str = str(CHUNK_ID),
    document_id: str = str(DOCUMENT_ID),
) -> AgentWorkflowResearchResult:
    return AgentWorkflowResearchResult(
        evidence=[
            AgentWorkflowEvidence(
                chunk_id=chunk_id,
                document_id=document_id,
                content="Trusted evidence text.",
                score=0.95,
            )
        ],
        retrieval_count=1,
        tool_calls_used=1,
        model_id="mock:research-v1",
        usage=TokenUsage(
            input_tokens=10,
            output_tokens=5,
        ),
    )


def test_builder_creates_deterministic_application_owned_recommendation() -> None:
    first = WorkflowRecommendationBuilder.build(
        execution_id=EXECUTION_ID,
        research=_research(),
    )
    second = WorkflowRecommendationBuilder.build(
        execution_id=EXECUTION_ID,
        research=_research(),
    )

    assert first == second
    assert first.recommendation_id == second.recommendation_id
    assert first.proposed_action.action_type == "create_security_review"
    assert first.proposed_action.resource_type == "workflow_execution"
    assert first.proposed_action.resource_id == EXECUTION_ID
    assert first.score_context is None
    assert first.generator_model_id is None


def test_builder_projects_only_trusted_evidence_identity() -> None:
    recommendation = WorkflowRecommendationBuilder.build(
        execution_id=EXECUTION_ID,
        research=_research(),
    )

    assert len(recommendation.evidence) == 1
    assert recommendation.evidence[0].chunk_id == CHUNK_ID
    assert recommendation.evidence[0].document_id == DOCUMENT_ID


@pytest.mark.parametrize(
    ("chunk_id", "document_id"),
    [
        ("not-a-uuid", str(DOCUMENT_ID)),
        (str(CHUNK_ID), "not-a-uuid"),
    ],
)
def test_builder_fails_closed_for_non_uuid_evidence_identity(
    chunk_id: str,
    document_id: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="requires UUID-backed trusted evidence",
    ):
        WorkflowRecommendationBuilder.build(
            execution_id=EXECUTION_ID,
            research=_research(
                chunk_id=chunk_id,
                document_id=document_id,
            ),
        )
