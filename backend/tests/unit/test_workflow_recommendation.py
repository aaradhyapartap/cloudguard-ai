"""Unit tests for the trusted Phase 7 workflow recommendation contract."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.models.approval import ApprovalAction, ApprovalEvidenceReference
from app.models.workflow_recommendation import WorkflowRecommendation
from pydantic import ValidationError

RECOMMENDATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
EXECUTION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CHUNK_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
DOCUMENT_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


def _action() -> ApprovalAction:
    return ApprovalAction(
        action_type="create_security_review",
        resource_type="workflow_execution",
        resource_id=EXECUTION_ID,
        summary="Create a bounded security review for this workflow execution.",
    )


def test_workflow_recommendation_accepts_bounded_application_action() -> None:
    recommendation = WorkflowRecommendation(
        recommendation_id=RECOMMENDATION_ID,
        proposed_action=_action(),
        evidence=(
            ApprovalEvidenceReference(
                chunk_id=CHUNK_ID,
                document_id=DOCUMENT_ID,
            ),
        ),
        score_context=None,
        generator_model_id=None,
    )

    assert recommendation.recommendation_id == RECOMMENDATION_ID
    assert recommendation.proposed_action.action_type == "create_security_review"
    assert recommendation.proposed_action.resource_id == EXECUTION_ID
    assert recommendation.score_context is None
    assert recommendation.generator_model_id is None


def test_workflow_recommendation_is_frozen_and_forbids_extra_fields() -> None:
    recommendation = WorkflowRecommendation(
        recommendation_id=RECOMMENDATION_ID,
        proposed_action=_action(),
    )

    with pytest.raises(ValidationError):
        recommendation.recommendation_id = EXECUTION_ID  # type: ignore[misc]

    with pytest.raises(ValidationError):
        WorkflowRecommendation(
            recommendation_id=RECOMMENDATION_ID,
            proposed_action=_action(),
            task_token="must-never-enter-recommendation-contract",
        )


def test_workflow_recommendation_rejects_duplicate_evidence() -> None:
    evidence = ApprovalEvidenceReference(
        chunk_id=CHUNK_ID,
        document_id=DOCUMENT_ID,
    )

    with pytest.raises(
        ValidationError,
        match="Workflow recommendation evidence must be unique",
    ):
        WorkflowRecommendation(
            recommendation_id=RECOMMENDATION_ID,
            proposed_action=_action(),
            evidence=(evidence, evidence),
        )
