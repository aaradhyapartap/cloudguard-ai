"""Phase 7.5 audit and bounded-action contract tests."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.models.approval import ApprovalAction
from app.models.approval_audit import ApprovalAuditEvent
from app.models.approved_action_execution import ApprovedActionProjection
from app.models.enums import ApprovalDecision
from app.services.approved_action_policy import (
    BoundedActionValidationError,
    validate_phase7_demo_action,
)

APPROVAL_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
EXECUTION_ID = UUID("22222222-2222-4222-8222-222222222222")


def _projection(
    *,
    action_type: str = "create_security_review",
    resource_id: UUID = EXECUTION_ID,
) -> ApprovedActionProjection:
    return ApprovedActionProjection(
        approval_id=APPROVAL_ID,
        organization_id=ORG_ID,
        workflow_execution_id=str(EXECUTION_ID),
        decision=ApprovalDecision.APPROVED,
        action=ApprovalAction(
            action_type=action_type,
            resource_type="workflow_execution",
            resource_id=resource_id,
            summary="Create bounded security review.",
        ),
        idempotency_key="approval:test:key",
    )


def test_audit_contract_has_no_task_token_field() -> None:
    assert "task_token" not in ApprovalAuditEvent.model_fields
    assert "taskToken" not in ApprovalAuditEvent.model_fields


def test_bounded_policy_accepts_security_review() -> None:
    action = validate_phase7_demo_action(
        _projection()
    )

    assert action.action_type == "create_security_review"
    assert action.resource_id == EXECUTION_ID


def test_bounded_policy_rejects_other_action_type() -> None:
    with pytest.raises(
        BoundedActionValidationError,
        match="outside the Phase 7 execution allowlist",
    ):
        validate_phase7_demo_action(
            _projection(
                action_type="delete_resource"
            )
        )


def test_bounded_policy_rejects_resource_substitution() -> None:
    with pytest.raises(
        BoundedActionValidationError,
        match="does not match the workflow execution",
    ):
        validate_phase7_demo_action(
            _projection(
                resource_id=UUID(
                    "99999999-9999-4999-8999-999999999999"
                )
            )
        )
