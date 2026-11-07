"""Unit tests for the local Approval callback adapter."""

from __future__ import annotations

import pytest
from app.adapters.local.approval_callback import LocalApprovalCallback
from app.models.approval import ApprovalAction
from app.models.enums import ApprovalDecision


def _action() -> ApprovalAction:
    return ApprovalAction(
        action_type="create_ticket",
        resource_type="security_finding",
        resource_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        summary="Create a security review ticket.",
    )


@pytest.mark.asyncio
async def test_local_callback_records_approved_decision() -> None:
    callback = LocalApprovalCallback()
    action = _action()

    await callback.send_decision(
        task_token="internal-token-1",
        decision=ApprovalDecision.APPROVED,
        effective_action=action,
    )

    assert len(callback.calls) == 1
    assert callback.calls[0].task_token == "internal-token-1"
    assert callback.calls[0].decision is ApprovalDecision.APPROVED
    assert callback.calls[0].effective_action == action


@pytest.mark.asyncio
async def test_local_callback_records_rejected_without_action() -> None:
    callback = LocalApprovalCallback()

    await callback.send_decision(
        task_token="internal-token-2",
        decision=ApprovalDecision.REJECTED,
        effective_action=None,
    )

    assert len(callback.calls) == 1
    assert callback.calls[0].decision is ApprovalDecision.REJECTED
    assert callback.calls[0].effective_action is None
