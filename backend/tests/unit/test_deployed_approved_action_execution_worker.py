"""Tests for the deployed approved-action execution worker boundary."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.deployed_approved_action_execution_worker import (
    ApprovedActionExecutionWorkflowTaskInput,
)
from pydantic import ValidationError

EXECUTION_ID = UUID("22222222-2222-4222-8222-222222222222")
ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")


def _event() -> dict[str, object]:
    return {
        "execution_id": str(EXECUTION_ID),
        "principal": {
            "user_id": str(USER_ID),
            "organization_id": str(ORG_ID),
            "role": "manager",
            "email": "manager@example.test",
            "department": "Security",
        },
    }


def test_worker_accepts_only_execution_identity_and_principal() -> None:
    parsed = ApprovedActionExecutionWorkflowTaskInput.model_validate(
        _event()
    )

    assert parsed.execution_id == EXECUTION_ID
    assert parsed.principal.organization_id == ORG_ID


def test_worker_rejects_supplied_action() -> None:
    event = _event()
    event["action"] = {
        "action_type": "delete_resource",
        "resource_type": "workflow_execution",
        "resource_id": str(EXECUTION_ID),
        "summary": "forged",
    }

    with pytest.raises(ValidationError):
        ApprovedActionExecutionWorkflowTaskInput.model_validate(
            event
        )


def test_worker_rejects_supplied_organization_override() -> None:
    event = _event()
    event["organization_id"] = str(
        UUID("99999999-9999-4999-8999-999999999999")
    )

    with pytest.raises(ValidationError):
        ApprovedActionExecutionWorkflowTaskInput.model_validate(
            event
        )
