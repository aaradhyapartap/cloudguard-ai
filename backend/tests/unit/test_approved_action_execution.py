"""Tests for the Phase 7.5 bounded approved-action execution service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.adapters.local.approved_action_executor import (
    LocalSecurityReviewExecutor,
)
from app.models.approval import ApprovalAction, DecidedApproval
from app.models.approved_action_execution import ActionExecutionDisposition
from app.models.enums import ApprovalDecision, ApprovalStatus
from app.services.approved_action_execution import ApprovedActionExecutionService

APPROVAL_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
EXECUTION_ID = UUID("22222222-2222-4222-8222-222222222222")
RECOMMENDATION_ID = UUID("33333333-3333-4333-8333-333333333333")
APPROVER_ID = UUID("44444444-4444-4444-8444-444444444444")


def _action(
    *,
    action_type: str = "create_security_review",
    resource_id: UUID = EXECUTION_ID,
    summary: str = "Create review.",
) -> ApprovalAction:
    return ApprovalAction(
        action_type=action_type,
        resource_type="workflow_execution",
        resource_id=resource_id,
        summary=summary,
    )


def _approval(
    *,
    decision: ApprovalDecision,
    proposed_action: ApprovalAction | None = None,
    modified_action: ApprovalAction | None = None,
) -> DecidedApproval:
    return DecidedApproval(
        id=APPROVAL_ID,
        organization_id=ORG_ID,
        workflow_execution_id=str(EXECUTION_ID),
        recommendation_id=RECOMMENDATION_ID,
        proposed_action=proposed_action or _action(),
        evidence=(),
        score_context=None,
        agent_trace_ids=(),
        generator_model_id=None,
        reviewer_model_id="mock:reviewer",
        status=ApprovalStatus.DECIDED,
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


@pytest.mark.asyncio
async def test_approved_executes_original_action() -> None:
    executor = LocalSecurityReviewExecutor()
    service = ApprovedActionExecutionService(
        executor=executor
    )

    result = await service.execute(
        approval=_approval(
            decision=ApprovalDecision.APPROVED
        )
    )

    assert result.disposition is ActionExecutionDisposition.SUCCEEDED
    assert result.action_type == "create_security_review"
    assert result.resource_id == EXECUTION_ID


@pytest.mark.asyncio
async def test_rejected_executes_nothing() -> None:
    class ExplodingExecutor:
        async def execute(self, **_: object):
            raise AssertionError(
                "Rejected Approval reached executor."
            )

    service = ApprovedActionExecutionService(
        executor=ExplodingExecutor()
    )

    result = await service.execute(
        approval=_approval(
            decision=ApprovalDecision.REJECTED
        )
    )

    assert result.disposition is ActionExecutionDisposition.SKIPPED
    assert result.action_type is None
    assert result.resource_id is None


@pytest.mark.asyncio
async def test_modified_executes_only_replacement() -> None:
    replacement = _action(
        summary="Manager replacement."
    )

    service = ApprovedActionExecutionService(
        executor=LocalSecurityReviewExecutor()
    )

    result = await service.execute(
        approval=_approval(
            decision=ApprovalDecision.MODIFIED,
            modified_action=replacement,
        )
    )

    assert result.disposition is ActionExecutionDisposition.SUCCEEDED
    assert result.resource_id == replacement.resource_id


@pytest.mark.asyncio
async def test_executor_replay_does_not_repeat_side_effect() -> None:
    executor = LocalSecurityReviewExecutor()
    service = ApprovedActionExecutionService(
        executor=executor
    )
    approval = _approval(
        decision=ApprovalDecision.APPROVED
    )

    first = await service.execute(
        approval=approval
    )
    second = await service.execute(
        approval=approval
    )

    assert first.disposition is ActionExecutionDisposition.SUCCEEDED
    assert second.disposition is ActionExecutionDisposition.REPLAYED
    assert first.execution_reference == second.execution_reference


@pytest.mark.asyncio
async def test_unapproved_action_type_fails_closed() -> None:
    service = ApprovedActionExecutionService(
        executor=LocalSecurityReviewExecutor()
    )

    with pytest.raises(
        ValueError,
        match="outside the Phase 7 execution allowlist",
    ):
        await service.execute(
            approval=_approval(
                decision=ApprovalDecision.APPROVED,
                proposed_action=_action(
                    action_type="delete_resource"
                ),
            )
        )


@pytest.mark.asyncio
async def test_action_resource_must_equal_workflow_execution() -> None:
    service = ApprovedActionExecutionService(
        executor=LocalSecurityReviewExecutor()
    )

    with pytest.raises(
        ValueError,
        match="does not match the workflow execution",
    ):
        await service.execute(
            approval=_approval(
                decision=ApprovalDecision.APPROVED,
                proposed_action=_action(
                    resource_id=UUID(
                        "99999999-9999-4999-8999-999999999999"
                    )
                ),
            )
        )
