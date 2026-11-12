"""Tests for the scheduled deployed Approval callback recovery worker."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import app.deployed_approval_callback_recovery_worker as worker
import pytest
from app.models.approval import ApprovalAction
from app.models.enums import ApprovalDecision
from app.ports.approval_callback_recovery_repository import (
    ApprovalCallbackClaim,
)

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
APPROVAL_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RESOURCE_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class FakeRepository:
    def __init__(self) -> None:
        self.claimed = False

    async def list_organization_ids(
        self,
        *,
        after_id: UUID | None = None,
        limit: int = 500,
    ) -> tuple[UUID, ...]:
        assert after_id is None
        assert limit == worker.ORGANIZATION_LIMIT
        return (ORG_ID,)

    async def list_recoverable_approval_ids(
        self,
        *,
        organization_id: UUID,
        stale_before: datetime,
        limit: int = 25,
    ) -> tuple[UUID, ...]:
        assert organization_id == ORG_ID
        assert isinstance(stale_before, datetime)
        assert limit == worker.APPROVAL_BATCH_SIZE
        return (APPROVAL_ID,)

    async def claim_decided_callback(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        stale_before: datetime,
    ) -> ApprovalCallbackClaim | None:
        assert organization_id == ORG_ID
        assert approval_id == APPROVAL_ID
        assert isinstance(stale_before, datetime)

        if self.claimed:
            return None

        self.claimed = True

        return ApprovalCallbackClaim(
            claim_id=uuid4(),
            approval_id=APPROVAL_ID,
            organization_id=ORG_ID,
            task_token="server-owned-recovery-token",
            decision=ApprovalDecision.APPROVED,
            effective_action=ApprovalAction(
                action_type="create_security_review",
                resource_type="workflow_execution",
                resource_id=RESOURCE_ID,
                summary="Create a bounded security review.",
            ),
        )

    async def mark_callback_delivered(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        claim_id: UUID,
    ) -> bool:
        assert organization_id == ORG_ID
        assert approval_id == APPROVAL_ID
        assert isinstance(claim_id, UUID)
        return True

    async def release_callback_claim(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        claim_id: UUID,
    ) -> bool:
        del organization_id, approval_id, claim_id
        return True


class FakeCallback:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def send_decision(
        self,
        *,
        task_token: str,
        decision: ApprovalDecision,
        effective_action: ApprovalAction | None,
    ) -> None:
        assert decision is ApprovalDecision.APPROVED
        assert effective_action is not None
        self.tokens.append(task_token)


@pytest.mark.asyncio
async def test_run_recovers_server_discovered_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeRepository()
    callback = FakeCallback()

    monkeypatch.setattr(
        worker,
        "_build_repository",
        lambda: repository,
    )
    monkeypatch.setattr(
        worker,
        "_build_callback",
        lambda: callback,
    )

    result = await worker._run()

    assert result == {
        "organizations": 1,
        "candidates": 1,
        "delivered": 1,
        "failed": 0,
    }
    assert callback.tokens == [
        "server-owned-recovery-token"
    ]


def test_handler_ignores_untrusted_scheduler_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run() -> dict[str, int]:
        return {
            "organizations": 0,
            "candidates": 0,
            "delivered": 0,
            "failed": 0,
        }

    monkeypatch.setattr(
        worker,
        "_run",
        fake_run,
    )

    result = worker.handler(
        {
            "task_token": "attacker-controlled-token",
            "organization_id": str(ORG_ID),
        },
        None,
    )

    assert result["delivered"] == 0


@pytest.mark.parametrize("env_name", ["dev", "staging", "prod"])
def test_callback_recovery_worker_starts_with_synthesized_env_without_cognito_or_guardrail(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
) -> None:
    """Callback recovery worker starts in dev/staging/prod without Cognito or Guardrail."""
    from app.adapters.aws.approval_callback import StepFunctionsApprovalCallback
    from app.adapters.aws.approval_callback_recovery_repository import (
        AuroraDataAPIApprovalCallbackRecoveryRepository,
    )
    from app.core.config import get_approval_worker_settings

    for key in (
        "IDENTITY_PROVIDER",
        "COGNITO_USER_POOL_ID",
        "COGNITO_CLIENT_ID",
        "CORS_ORIGINS",
        "BEDROCK_GUARDRAIL_ID",
        "BEDROCK_GUARDRAIL_VERSION",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("ENVIRONMENT", env_name)
    monkeypatch.setenv(
        "AWS_AURORA_CLUSTER_ARN",
        "arn:aws:rds:us-east-1:111122223333:cluster:cloudguard",
    )
    monkeypatch.setenv(
        "AWS_AURORA_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:111122223333:secret:cloudguard",
    )
    monkeypatch.setenv("DB_NAME", "cloudguard")
    monkeypatch.setenv("ENABLE_AGENTIC_WORKFLOWS", "true")
    get_approval_worker_settings.cache_clear()

    try:
        repo = worker._build_repository()
        cb = worker._build_callback()
        assert isinstance(repo, AuroraDataAPIApprovalCallbackRecoveryRepository)
        assert isinstance(cb, StepFunctionsApprovalCallback)
    finally:
        get_approval_worker_settings.cache_clear()
