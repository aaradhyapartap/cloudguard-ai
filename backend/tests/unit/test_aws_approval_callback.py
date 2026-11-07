"""Unit tests for the AWS Step Functions approval callback adapter."""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.adapters.aws.approval_callback import StepFunctionsApprovalCallback
from app.core.errors import UpstreamError
from app.models.approval import ApprovalAction
from app.models.enums import ApprovalDecision


class RecordingStepFunctionsClient:
    def __init__(
        self,
        *,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def send_task_success(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return {}


def _action() -> ApprovalAction:
    return ApprovalAction(
        action_type="create_ticket",
        resource_type="security_finding",
        resource_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        summary="Create a security review ticket.",
    )


@pytest.mark.asyncio
async def test_aws_callback_sends_validated_approved_payload() -> None:
    client = RecordingStepFunctionsClient()
    callback = StepFunctionsApprovalCallback(client=client)

    await callback.send_decision(
        task_token="internal-token-1",
        decision=ApprovalDecision.APPROVED,
        effective_action=_action(),
    )

    assert len(client.calls) == 1
    assert client.calls[0]["taskToken"] == "internal-token-1"

    output = json.loads(client.calls[0]["output"])
    assert output["decision"] == "approved"
    assert output["effective_action"]["action_type"] == "create_ticket"
    assert (
        output["effective_action"]["resource_id"]
        == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )


@pytest.mark.asyncio
async def test_aws_callback_sends_rejected_without_action() -> None:
    client = RecordingStepFunctionsClient()
    callback = StepFunctionsApprovalCallback(client=client)

    await callback.send_decision(
        task_token="internal-token-2",
        decision=ApprovalDecision.REJECTED,
        effective_action=None,
    )

    output = json.loads(client.calls[0]["output"])

    assert output == {
        "decision": "rejected",
        "effective_action": None,
    }


@pytest.mark.asyncio
async def test_aws_callback_normalizes_provider_failure() -> None:
    client = RecordingStepFunctionsClient(
        error=RuntimeError(
            "provider detail containing internal-token-secret"
        )
    )
    callback = StepFunctionsApprovalCallback(client=client)

    with pytest.raises(
        UpstreamError,
        match="Could not resume the approval workflow",
    ) as caught:
        await callback.send_decision(
            task_token="internal-token-secret",
            decision=ApprovalDecision.REJECTED,
            effective_action=None,
        )

    assert "internal-token-secret" not in str(caught.value)
    assert len(client.calls) == 1
