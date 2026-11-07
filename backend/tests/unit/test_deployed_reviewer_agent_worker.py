"""Unit tests for the deployed Phase 6 Reviewer workflow task."""

from __future__ import annotations

import pytest
from app.deployed_reviewer_agent_worker import ReviewerWorkflowTaskInput
from pydantic import ValidationError


def _valid_event() -> dict[str, object]:
    return {
        "execution_id": "exec-123",
        "correlation_id": "corr-123",
        "question": "Should this workflow pass review?",
        "principal": {
            "user_id": "00000000-0000-4000-8000-000000000001",
            "organization_id": "00000000-0000-4000-8000-000000000002",
            "role": "manager",
            "email": "manager@cloudguard.ai",
            "department": "Security",
        },
        "research": {
            "evidence": [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "content": "Trusted workflow evidence.",
                    "score": 0.95,
                }
            ],
            "retrieval_count": 1,
            "tool_calls_used": 1,
            "model_id": "mock:research",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
            },
        },
        "risk": {
            "estimates": [
                {
                    "chunk_id": "chunk-1",
                    "likelihood": 0.4,
                    "impact": 0.7,
                    "rationale": "Evidence supports a bounded estimate.",
                }
            ],
            "evidence_count": 1,
            "model_id": "mock:risk",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
            },
        },
    }


def test_reviewer_task_input_parses_prior_workflow_state() -> None:
    parsed = ReviewerWorkflowTaskInput.model_validate(_valid_event())

    assert parsed.execution_id == "exec-123"
    assert parsed.correlation_id == "corr-123"
    assert parsed.question == "Should this workflow pass review?"
    assert len(parsed.research.evidence) == 1
    assert len(parsed.risk.estimates) == 1


def test_reviewer_task_input_rejects_unknown_fields() -> None:
    event = _valid_event()
    event["unexpected"] = "not allowed"

    with pytest.raises(ValidationError):
        ReviewerWorkflowTaskInput.model_validate(event)


def test_reviewer_task_input_rejects_missing_risk_state() -> None:
    event = _valid_event()
    del event["risk"]

    with pytest.raises(ValidationError):
        ReviewerWorkflowTaskInput.model_validate(event)


def test_reviewer_task_input_rejects_malformed_risk_state() -> None:
    event = _valid_event()
    event["risk"] = {"estimates": "not-a-list"}

    with pytest.raises(ValidationError):
        ReviewerWorkflowTaskInput.model_validate(event)
