"""Tests for human-decision approved-action projection."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.models.approval import ApprovalAction, DecidedApproval
from app.models.enums import ApprovalDecision, ApprovalStatus
from app.services.approved_action_projection import ApprovedActionProjector

APPROVAL_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
EXECUTION_ID = UUID("22222222-2222-4222-8222-222222222222")
RECOMMENDATION_ID = UUID("33333333-3333-4333-8333-333333333333")
APPROVER_ID = UUID("44444444-4444-4444-8444-444444444444")


def _action(*, summary: str = "Create review.") -> ApprovalAction:
    return ApprovalAction(
        action_type="create_security_review",
        resource_type="workflow_execution",
        resource_id=EXECUTION_ID,
        summary=summary,
    )


def _approval(
    *,
    decision: ApprovalDecision,
    modified_action: ApprovalAction | None = None,
    status: ApprovalStatus = ApprovalStatus.DECIDED,
) -> DecidedApproval:
    return DecidedApproval(
        id=APPROVAL_ID,
        organization_id=ORG_ID,
        workflow_execution_id=str(EXECUTION_ID),
        recommendation_id=RECOMMENDATION_ID,
        proposed_action=_action(summary="Original."),
        evidence=(),
        score_context=None,
        agent_trace_ids=(),
        generator_model_id=None,
        reviewer_model_id="mock:reviewer",
        status=status,
        created_at=datetime(2026, 9, 6, tzinfo=UTC),
        decision=decision,
        approver_id=APPROVER_ID,
        decided_at=datetime(2026, 9, 6, 1, tzinfo=UTC),
        justification=(
            "Replacement requested."
            if decision is ApprovalDecision.MODIFIED
            else (
                "Manager rejected the proposed action."
                if decision is ApprovalDecision.REJECTED
                else None
            )
        ),
        comment=None,
        modified_action=modified_action,
    )


def test_approved_projects_original_action() -> None:
    projected = ApprovedActionProjector.project(
        _approval(decision=ApprovalDecision.APPROVED)
    )

    assert projected.action == _action(summary="Original.")
    assert projected.decision is ApprovalDecision.APPROVED


def test_modified_projects_only_human_replacement() -> None:
    replacement = _action(summary="Manager replacement.")

    projected = ApprovedActionProjector.project(
        _approval(
            decision=ApprovalDecision.MODIFIED,
            modified_action=replacement,
        )
    )

    assert projected.action == replacement
    assert projected.action != _action(summary="Original.")


def test_rejected_projects_no_action() -> None:
    projected = ApprovedActionProjector.project(
        _approval(decision=ApprovalDecision.REJECTED)
    )

    assert projected.action is None


def test_projection_key_is_deterministic() -> None:
    approval = _approval(
        decision=ApprovalDecision.APPROVED
    )

    first = ApprovedActionProjector.project(approval)
    second = ApprovedActionProjector.project(approval)

    assert first.idempotency_key == second.idempotency_key
    assert str(APPROVAL_ID) in first.idempotency_key


def test_terminal_approval_cannot_reenter_execution() -> None:
    with pytest.raises(
        ValueError,
        match="Only a decided Approval",
    ):
        ApprovedActionProjector.project(
            _approval(
                decision=ApprovalDecision.APPROVED,
                status=ApprovalStatus.EXECUTION_SUCCEEDED,
            )
        )


def test_invalid_modified_decision_is_rejected_before_projection() -> None:
    with pytest.raises(
        ValueError,
        match="Modified decision requires a replacement action",
    ):
        _approval(
            decision=ApprovalDecision.MODIFIED,
            modified_action=None,
        )
