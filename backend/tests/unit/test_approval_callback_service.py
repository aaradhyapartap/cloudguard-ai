"""Unit tests for leased post-commit Approval callback recovery."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.adapters.local.approval_callback import LocalApprovalCallback
from app.models.approval import ApprovalAction
from app.models.enums import ApprovalDecision
from app.ports.approval_callback_recovery_repository import (
    ApprovalCallbackClaim,
)
from app.services.approval_callback import ApprovalCallbackService

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
APPROVAL_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RESOURCE_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
STALE_BEFORE = datetime(2026, 9, 6, 7, 0, tzinfo=UTC)


def _action() -> ApprovalAction:
    return ApprovalAction(
        action_type="create_ticket",
        resource_type="security_finding",
        resource_id=RESOURCE_ID,
        summary="Create a security review ticket.",
    )


class CallbackRecoveryRepository:
    def __init__(
        self,
        claim: ApprovalCallbackClaim | None,
    ) -> None:
        self.claim = claim
        self.claim_requests: list[
            tuple[UUID, UUID, datetime]
        ] = []
        self.delivered_requests: list[
            tuple[UUID, UUID, UUID]
        ] = []
        self.release_requests: list[
            tuple[UUID, UUID, UUID]
        ] = []
        self.mark_delivered_result = True

    async def list_organization_ids(
        self,
        *,
        limit: int = 500,
    ) -> tuple[UUID, ...]:
        del limit
        return ()

    async def list_recoverable_approval_ids(
        self,
        *,
        organization_id: UUID,
        stale_before: datetime,
        limit: int = 25,
    ) -> tuple[UUID, ...]:
        del organization_id, stale_before, limit
        return ()

    async def claim_decided_callback(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        stale_before: datetime,
    ) -> ApprovalCallbackClaim | None:
        self.claim_requests.append(
            (
                organization_id,
                approval_id,
                stale_before,
            )
        )

        claim = self.claim
        self.claim = None
        return claim

    async def mark_callback_delivered(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        claim_id: UUID,
    ) -> bool:
        self.delivered_requests.append(
            (
                organization_id,
                approval_id,
                claim_id,
            )
        )
        return self.mark_delivered_result

    async def release_callback_claim(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        claim_id: UUID,
    ) -> bool:
        self.release_requests.append(
            (
                organization_id,
                approval_id,
                claim_id,
            )
        )
        return True


@pytest.mark.asyncio
async def test_resume_uses_repository_owned_token_and_claim() -> None:
    action = _action()
    claim_id = uuid4()

    repository = CallbackRecoveryRepository(
        ApprovalCallbackClaim(
            claim_id=claim_id,
            approval_id=APPROVAL_ID,
            organization_id=ORG_ID,
            task_token="server-owned-secret-token",
            decision=ApprovalDecision.APPROVED,
            effective_action=action,
        )
    )

    callback = LocalApprovalCallback()

    service = ApprovalCallbackService(
        repository=repository,
        callback=callback,
    )

    resumed = await service.resume_decided(
        organization_id=ORG_ID,
        approval_id=APPROVAL_ID,
        stale_before=STALE_BEFORE,
    )

    assert resumed is True

    assert repository.claim_requests == [
        (
            ORG_ID,
            APPROVAL_ID,
            STALE_BEFORE,
        )
    ]

    assert repository.delivered_requests == [
        (
            ORG_ID,
            APPROVAL_ID,
            claim_id,
        )
    ]

    assert repository.release_requests == []

    assert len(callback.calls) == 1
    assert callback.calls[0].task_token == "server-owned-secret-token"


@pytest.mark.asyncio
async def test_resume_does_nothing_when_claim_is_unavailable() -> None:
    repository = CallbackRecoveryRepository(
        None
    )

    callback = LocalApprovalCallback()

    service = ApprovalCallbackService(
        repository=repository,
        callback=callback,
    )

    resumed = await service.resume_decided(
        organization_id=ORG_ID,
        approval_id=APPROVAL_ID,
        stale_before=STALE_BEFORE,
    )

    assert resumed is False
    assert callback.calls == []
    assert repository.delivered_requests == []
    assert repository.release_requests == []


@pytest.mark.asyncio
async def test_callback_failure_releases_only_owned_claim() -> None:
    claim_id = uuid4()

    repository = CallbackRecoveryRepository(
        ApprovalCallbackClaim(
            claim_id=claim_id,
            approval_id=APPROVAL_ID,
            organization_id=ORG_ID,
            task_token="server-owned-secret-token",
            decision=ApprovalDecision.REJECTED,
            effective_action=None,
        )
    )

    class FailingCallback:
        async def send_decision(
            self,
            *,
            task_token: str,
            decision: ApprovalDecision,
            effective_action: ApprovalAction | None,
        ) -> None:
            del task_token, decision, effective_action
            raise RuntimeError(
                "Step Functions unavailable"
            )

    service = ApprovalCallbackService(
        repository=repository,
        callback=FailingCallback(),
    )

    with pytest.raises(
        RuntimeError,
        match="Step Functions unavailable",
    ):
        await service.resume_decided(
            organization_id=ORG_ID,
            approval_id=APPROVAL_ID,
            stale_before=STALE_BEFORE,
        )

    assert repository.release_requests == [
        (
            ORG_ID,
            APPROVAL_ID,
            claim_id,
        )
    ]

    assert repository.delivered_requests == []


@pytest.mark.asyncio
async def test_delivery_marker_failure_fails_closed() -> None:
    claim_id = uuid4()

    repository = CallbackRecoveryRepository(
        ApprovalCallbackClaim(
            claim_id=claim_id,
            approval_id=APPROVAL_ID,
            organization_id=ORG_ID,
            task_token="server-owned-secret-token",
            decision=ApprovalDecision.APPROVED,
            effective_action=_action(),
        )
    )

    repository.mark_delivered_result = False

    service = ApprovalCallbackService(
        repository=repository,
        callback=LocalApprovalCallback(),
    )

    with pytest.raises(
        RuntimeError,
        match="delivery marker could not be persisted",
    ):
        await service.resume_decided(
            organization_id=ORG_ID,
            approval_id=APPROVAL_ID,
            stale_before=STALE_BEFORE,
        )
