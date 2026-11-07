"""Unit tests for the deployed Phase 6 Research workflow task."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.deployed_research_agent_worker import ResearchWorkflowTaskInput
from app.models.enums import Role
from pydantic import ValidationError


def _valid_event() -> dict[str, object]:
    return {
        "execution_id": "exec-123",
        "correlation_id": "corr-123",
        "question": "What evidence supports this risk?",
        "principal": {
            "user_id": "00000000-0000-4000-8000-000000000001",
            "organization_id": "00000000-0000-4000-8000-000000000002",
            "role": "analyst",
            "email": "analyst@cloudguard.ai",
            "department": "Security",
        },
    }


def test_research_task_input_parses_principal_and_metadata() -> None:
    parsed = ResearchWorkflowTaskInput.model_validate(_valid_event())

    assert parsed.execution_id == "exec-123"
    assert parsed.correlation_id == "corr-123"
    assert parsed.question == "What evidence supports this risk?"
    assert parsed.principal.user_id == UUID(
        "00000000-0000-4000-8000-000000000001"
    )
    assert parsed.principal.organization_id == UUID(
        "00000000-0000-4000-8000-000000000002"
    )
    assert parsed.principal.role is Role.ANALYST


def test_research_task_input_rejects_unknown_fields() -> None:
    event = _valid_event()
    event["unexpected"] = "not allowed"

    with pytest.raises(ValidationError):
        ResearchWorkflowTaskInput.model_validate(event)


def test_research_task_input_rejects_missing_principal() -> None:
    event = _valid_event()
    del event["principal"]

    with pytest.raises(ValidationError):
        ResearchWorkflowTaskInput.model_validate(event)


def test_research_task_input_rejects_blank_question() -> None:
    event = _valid_event()
    event["question"] = ""

    with pytest.raises(ValidationError):
        ResearchWorkflowTaskInput.model_validate(event)
