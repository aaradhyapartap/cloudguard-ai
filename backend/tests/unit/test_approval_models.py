"""Unit tests for Phase 7 approval contracts and state transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from app.models.approval import (
    ApprovalAction,
    ApprovalDecisionRequest,
    ApprovalEvidenceReference,
    ApprovalScoreContext,
    DecidedApproval,
    PendingApproval,
    validate_approval_transition,
)
from app.models.enums import ApprovalDecision, ApprovalStatus
from pydantic import ValidationError

APPROVAL_ID = UUID("11111111-1111-4111-8111-111111111111")
ORG_ID = UUID("22222222-2222-4222-8222-222222222222")
RECOMMENDATION_ID = UUID("33333333-3333-4333-8333-333333333333")
RESOURCE_ID = UUID("44444444-4444-4444-8444-444444444444")
CHUNK_ID = UUID("55555555-5555-4555-8555-555555555555")
DOCUMENT_ID = UUID("66666666-6666-4666-8666-666666666666")
MANAGER_ID = UUID("77777777-7777-4777-8777-777777777777")


def _action() -> ApprovalAction:
    return ApprovalAction(
        action_type="escalate_risk",
        resource_type="risk",
        resource_id=RESOURCE_ID,
        summary="Escalate the reviewed risk for Manager-approved follow-up.",
    )


def _pending_payload() -> dict[str, object]:
    return {
        "id": APPROVAL_ID,
        "organization_id": ORG_ID,
        "workflow_execution_id": "execution-123",
        "recommendation_id": RECOMMENDATION_ID,
        "proposed_action": _action(),
        "evidence": [
            ApprovalEvidenceReference(
                chunk_id=CHUNK_ID,
                document_id=DOCUMENT_ID,
            )
        ],
        "score_context": ApprovalScoreContext(
            score=Decimal("72.50"),
            scoring_version="v1.0",
        ),
        "agent_trace_ids": ["research-1", "risk-1", "reviewer-1"],
        "generator_model_id": "mock:generator-v1",
        "reviewer_model_id": "mock:reviewer-v1",
        "status": ApprovalStatus.PENDING,
        "created_at": datetime.now(UTC),
    }


def test_pending_approval_accepts_bounded_valid_context() -> None:
    approval = PendingApproval.model_validate(_pending_payload())

    assert approval.status is ApprovalStatus.PENDING
    assert approval.proposed_action.action_type == "escalate_risk"
    assert approval.score_context is not None
    assert approval.score_context.score == Decimal("72.50")


def test_pending_approval_is_frozen() -> None:
    approval = PendingApproval.model_validate(_pending_payload())

    with pytest.raises(ValidationError):
        approval.status = ApprovalStatus.DECIDED  # type: ignore[misc]


def test_pending_approval_rejects_non_pending_status() -> None:
    payload = _pending_payload()
    payload["status"] = ApprovalStatus.DECIDED

    with pytest.raises(ValidationError, match="must be pending"):
        PendingApproval.model_validate(payload)


@pytest.mark.parametrize(
    "forbidden_field,value",
    [
        ("task_token", "secret-token"),
        ("approver_id", str(MANAGER_ID)),
        ("role", "manager"),
        ("permissions", ["approval:decide"]),
    ],
)
def test_decision_request_rejects_server_owned_identity_fields(
    forbidden_field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "decision": ApprovalDecision.APPROVED.value,
        forbidden_field: value,
    }

    with pytest.raises(ValidationError):
        ApprovalDecisionRequest.model_validate(payload)


def test_approved_decision_rejects_modified_action() -> None:
    with pytest.raises(
        ValidationError,
        match="cannot include a modified action",
    ):
        ApprovalDecisionRequest(
            decision=ApprovalDecision.APPROVED,
            modified_action=_action(),
        )


def test_rejected_decision_requires_justification() -> None:
    with pytest.raises(
        ValidationError,
        match="requires justification",
    ):
        ApprovalDecisionRequest(
            decision=ApprovalDecision.REJECTED,
        )


def test_rejected_decision_rejects_modified_action() -> None:
    with pytest.raises(
        ValidationError,
        match="cannot include a modified action",
    ):
        ApprovalDecisionRequest(
            decision=ApprovalDecision.REJECTED,
            justification="The evidence does not support this action.",
            modified_action=_action(),
        )


def test_modified_decision_requires_justification() -> None:
    with pytest.raises(
        ValidationError,
        match="requires justification",
    ):
        ApprovalDecisionRequest(
            decision=ApprovalDecision.MODIFIED,
            modified_action=_action(),
        )


def test_modified_decision_requires_replacement_action() -> None:
    with pytest.raises(
        ValidationError,
        match="requires a replacement action",
    ):
        ApprovalDecisionRequest(
            decision=ApprovalDecision.MODIFIED,
            justification="Use a narrower escalation.",
        )


def test_valid_modified_decision_is_accepted() -> None:
    decision = ApprovalDecisionRequest(
        decision=ApprovalDecision.MODIFIED,
        justification="Use the Manager-reviewed bounded action.",
        modified_action=_action(),
    )

    assert decision.modified_action is not None


def test_decided_approval_requires_decided_status() -> None:
    payload = {
        **_pending_payload(),
        "status": ApprovalStatus.PENDING,
        "decision": ApprovalDecision.APPROVED,
        "approver_id": MANAGER_ID,
        "decided_at": datetime.now(UTC),
    }

    with pytest.raises(ValidationError, match="must be decided"):
        DecidedApproval.model_validate(payload)


def test_decided_modified_approval_requires_modified_action() -> None:
    payload = {
        **_pending_payload(),
        "status": ApprovalStatus.DECIDED,
        "decision": ApprovalDecision.MODIFIED,
        "approver_id": MANAGER_ID,
        "decided_at": datetime.now(UTC),
        "justification": "Manager changed the action.",
    }

    with pytest.raises(
        ValidationError,
        match="requires a replacement action",
    ):
        DecidedApproval.model_validate(payload)


def test_decided_approval_has_no_task_token_field() -> None:
    payload = {
        **_pending_payload(),
        "status": ApprovalStatus.DECIDED,
        "decision": ApprovalDecision.APPROVED,
        "approver_id": MANAGER_ID,
        "decided_at": datetime.now(UTC),
        "task_token": "must-never-be-public",
    }

    with pytest.raises(ValidationError):
        DecidedApproval.model_validate(payload)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ApprovalStatus.PENDING, ApprovalStatus.DECIDED),
        (ApprovalStatus.DECIDED, ApprovalStatus.EXECUTION_SUCCEEDED),
        (ApprovalStatus.DECIDED, ApprovalStatus.EXECUTION_FAILED),
    ],
)
def test_valid_state_transitions(
    current: ApprovalStatus,
    target: ApprovalStatus,
) -> None:
    validate_approval_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ApprovalStatus.PENDING, ApprovalStatus.EXECUTION_SUCCEEDED),
        (ApprovalStatus.PENDING, ApprovalStatus.EXECUTION_FAILED),
        (ApprovalStatus.DECIDED, ApprovalStatus.PENDING),
        (ApprovalStatus.EXECUTION_SUCCEEDED, ApprovalStatus.DECIDED),
        (ApprovalStatus.EXECUTION_FAILED, ApprovalStatus.DECIDED),
        (ApprovalStatus.EXECUTION_SUCCEEDED, ApprovalStatus.PENDING),
        (ApprovalStatus.EXECUTION_FAILED, ApprovalStatus.PENDING),
    ],
)
def test_invalid_state_transitions_fail_closed(
    current: ApprovalStatus,
    target: ApprovalStatus,
) -> None:
    with pytest.raises(ValueError, match="Invalid approval state transition"):
        validate_approval_transition(current, target)


def test_action_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ApprovalAction.model_validate(
            {
                "action_type": "escalate_risk",
                "resource_type": "risk",
                "resource_id": str(RESOURCE_ID),
                "summary": "Bounded action.",
                "arbitrary_tool": "send_email",
            }
        )


def test_action_summary_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ApprovalAction(
            action_type="escalate_risk",
            resource_type="risk",
            resource_id=RESOURCE_ID,
            summary="x" * 4001,
        )


def test_pending_approval_rejects_duplicate_evidence_chunks() -> None:
    payload = _pending_payload()
    duplicate = ApprovalEvidenceReference(
        chunk_id=CHUNK_ID,
        document_id=DOCUMENT_ID,
    )
    payload["evidence"] = [duplicate, duplicate]

    with pytest.raises(
        ValidationError,
        match="evidence chunk_id values must be unique",
    ):
        PendingApproval.model_validate(payload)


def test_decided_approval_rejects_duplicate_evidence_chunks() -> None:
    payload = {
        **_pending_payload(),
        "status": ApprovalStatus.DECIDED,
        "decision": ApprovalDecision.APPROVED,
        "approver_id": MANAGER_ID,
        "decided_at": datetime.now(UTC),
    }
    duplicate = ApprovalEvidenceReference(
        chunk_id=CHUNK_ID,
        document_id=DOCUMENT_ID,
    )
    payload["evidence"] = [duplicate, duplicate]

    with pytest.raises(
        ValidationError,
        match="evidence chunk_id values must be unique",
    ):
        DecidedApproval.model_validate(payload)


def test_evidence_list_is_bounded() -> None:
    payload = _pending_payload()
    payload["evidence"] = [
        ApprovalEvidenceReference(
            chunk_id=UUID(f"00000000-0000-4000-8000-{index:012d}"),
            document_id=DOCUMENT_ID,
        )
        for index in range(11)
    ]

    with pytest.raises(ValidationError):
        PendingApproval.model_validate(payload)
