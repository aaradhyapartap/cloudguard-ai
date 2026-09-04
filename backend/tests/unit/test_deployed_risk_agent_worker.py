"""Unit tests for the deployed Phase 6 Risk workflow task."""

from __future__ import annotations

import pytest
from app.deployed_risk_agent_worker import RiskWorkflowTaskInput
from pydantic import ValidationError


def _valid_event() -> dict[str, object]:
    return {
        "execution_id": "exec-123",
        "correlation_id": "corr-123",
        "question": "What is the risk?",
        "principal": {
            "user_id": "00000000-0000-4000-8000-000000000001",
            "organization_id": "00000000-0000-4000-8000-000000000002",
            "role": "analyst",
            "email": "analyst@cloudguard.ai",
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
    }


def test_risk_task_input_parses_research_state() -> None:
    parsed = RiskWorkflowTaskInput.model_validate(_valid_event())

    assert parsed.execution_id == "exec-123"
    assert parsed.correlation_id == "corr-123"
    assert parsed.question == "What is the risk?"
    assert len(parsed.research.evidence) == 1
    assert parsed.research.evidence[0].chunk_id == "chunk-1"


def test_risk_task_input_rejects_unknown_fields() -> None:
    event = _valid_event()
    event["unexpected"] = "not allowed"

    with pytest.raises(ValidationError):
        RiskWorkflowTaskInput.model_validate(event)


def test_risk_task_input_rejects_missing_research_state() -> None:
    event = _valid_event()
    del event["research"]

    with pytest.raises(ValidationError):
        RiskWorkflowTaskInput.model_validate(event)


def test_risk_task_input_rejects_malformed_research_state() -> None:
    event = _valid_event()
    event["research"] = {"evidence": "not-a-list"}

    with pytest.raises(ValidationError):
        RiskWorkflowTaskInput.model_validate(event)
