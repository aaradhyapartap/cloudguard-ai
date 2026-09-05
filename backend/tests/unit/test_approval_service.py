"""Focused unit tests for the Phase 7.3 ApprovalService boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.models.approval import (
    ApprovalAction,
    ApprovalDecisionRequest,
    ApprovalEvidenceReference,
    ApprovalScoreContext,
    DecidedApproval,
    PendingApproval,
)
from app.models.enums import ApprovalDecision, ApprovalStatus, Role
from app.models.principal import Principal
from app.ports.approval_repository import ApprovalRepository
from app.services.approval import ApprovalService

ORG_A = UUID("81111111-1111-4111-8111-111111111111")
ORG_B = UUID("82222222-2222-4222-8222-222222222222")

MANAGER_A = UUID("83333333-3333-4333-8333-333333333333")
MANAGER_B = UUID("84444444-4444-4444-8444-444444444444")
ANALYST_A = UUID("85555555-5555-4555-8555-555555555555")
ADMIN_A = UUID("86666666-6666-4666-8666-666666666666")

APPROVAL_ID = UUID("87777777-7777-4777-8777-777777777777")
RESOURCE_ID = UUID("88888888-8888-4888-8888-888888888888")
DOCUMENT_ID = UUID("89999999-9999-4999-8999-999999999999")
CHUNK_ID = UUID("8aaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _principal(
    *,
    user_id: UUID,
    organization_id: UUID,
    role: Role,
) -> Principal:
    return Principal(
        user_id=user_id,
        organization_id=organization_id,
        email=f"{role.value}@example.test",
        role=role,
    )


MANAGER = _principal(
    user_id=MANAGER_A,
    organization_id=ORG_A,
    role=Role.MANAGER,
)

OTHER_MANAGER = _principal(
    user_id=MANAGER_B,
    organization_id=ORG_B,
    role=Role.MANAGER,
)

ANALYST = _principal(
    user_id=ANALYST_A,
    organization_id=ORG_A,
    role=Role.ANALYST,
)

ADMIN = _principal(
    user_id=ADMIN_A,
    organization_id=ORG_A,
    role=Role.ADMIN,
)


def _action(summary: str = "Escalate reviewed risk.") -> ApprovalAction:
    return ApprovalAction(
        action_type="escalate_risk",
        resource_type="risk",
        resource_id=RESOURCE_ID,
        summary=summary,
    )


def _evidence() -> tuple[ApprovalEvidenceReference, ...]:
    return (
        ApprovalEvidenceReference(
            chunk_id=CHUNK_ID,
            document_id=DOCUMENT_ID,
        ),
    )


def _score_context() -> ApprovalScoreContext:
    return ApprovalScoreContext(
        score=Decimal("72.50"),
        scoring_version="v1.0",
        component_breakdown={
            "scoring_version": "v1.0",
            "total_controls": 4,
            "applicable_controls": 4,
            "sum_weights": "4.0",
            "sum_weighted_scores": "290.0",
            "critical_override_triggered": False,
        },
    )


def _pending(
    *,
    organization_id: UUID = ORG_A,
) -> PendingApproval:
    return PendingApproval(
        id=APPROVAL_ID,
        organization_id=organization_id,
        workflow_execution_id="workflow-1",
        recommendation_id=UUID("8bbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        proposed_action=_action(),
        evidence=_evidence(),
        score_context=_score_context(),
        agent_trace_ids=("research-1", "reviewer-1"),
        generator_model_id="mock:generator-v1",
        reviewer_model_id="mock:reviewer-v1",
        status=ApprovalStatus.PENDING,
        created_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
    )


def _decided(
    *,
    approver_id: UUID = MANAGER_A,
) -> DecidedApproval:
    pending = _pending()

    return DecidedApproval(
        **pending.model_dump(exclude={"status"}),
        status=ApprovalStatus.DECIDED,
        decision=ApprovalDecision.APPROVED,
        approver_id=approver_id,
        decided_at=datetime(2026, 9, 5, 12, 30, tzinfo=UTC),
        justification=None,
        comment=None,
        modified_action=None,
    )


class FakeApprovalRepository(ApprovalRepository):
    def __init__(self) -> None:
        self.pending: PendingApproval | None = _pending()
        self.decided: DecidedApproval | None = None
        self.force_cas_loss = False

        self.last_get_organization_id: UUID | None = None
        self.last_list_organization_id: UUID | None = None
        self.last_decide_organization_id: UUID | None = None
        self.last_approver_id: UUID | None = None

    async def create_pending(self, **kwargs: object) -> PendingApproval:
        raise NotImplementedError

    async def get_by_id(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
    ) -> PendingApproval | DecidedApproval | None:
        self.last_get_organization_id = organization_id

        current: PendingApproval | DecidedApproval | None
        if self.decided is not None:
            current = self.decided
        else:
            current = self.pending

        if current is None:
            return None

        if current.organization_id != organization_id:
            return None

        if current.id != approval_id:
            return None

        return current

    async def list_pending(
        self,
        *,
        organization_id: UUID,
        limit: int = 25,
    ) -> list[PendingApproval]:
        self.last_list_organization_id = organization_id

        if (
            self.pending is None
            or self.pending.organization_id != organization_id
        ):
            return []

        return [self.pending][:limit]

    async def decide_pending(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        decision: ApprovalDecision,
        approver_id: UUID,
        decided_at: datetime,
        justification: str | None,
        comment: str | None,
        modified_action: ApprovalAction | None,
    ) -> DecidedApproval | None:
        self.last_decide_organization_id = organization_id
        self.last_approver_id = approver_id

        if self.force_cas_loss:
            return None

        current = await self.get_by_id(
            organization_id=organization_id,
            approval_id=approval_id,
        )
        if not isinstance(current, PendingApproval):
            return None

        decided = DecidedApproval(
            **current.model_dump(exclude={"status"}),
            status=ApprovalStatus.DECIDED,
            decision=decision,
            approver_id=approver_id,
            decided_at=decided_at,
            justification=justification,
            comment=comment,
            modified_action=modified_action,
        )

        self.pending = None
        self.decided = decided
        return decided

    async def get_decided_callback_context(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_manager_can_list_pending_for_own_tenant() -> None:
    repo = FakeApprovalRepository()
    service = ApprovalService(repository=repo)

    result = await service.list_pending(
        principal=MANAGER,
        limit=10,
    )

    assert result == [_pending()]
    assert repo.last_list_organization_id == ORG_A


@pytest.mark.asyncio
async def test_admin_can_read_approval() -> None:
    repo = FakeApprovalRepository()
    service = ApprovalService(repository=repo)

    result = await service.get_approval(
        principal=ADMIN,
        approval_id=APPROVAL_ID,
    )

    assert isinstance(result, PendingApproval)
    assert repo.last_get_organization_id == ORG_A


@pytest.mark.asyncio
async def test_analyst_cannot_read_approval() -> None:
    repo = FakeApprovalRepository()
    service = ApprovalService(repository=repo)

    with pytest.raises(AuthorizationError):
        await service.get_approval(
            principal=ANALYST,
            approval_id=APPROVAL_ID,
        )


@pytest.mark.asyncio
async def test_analyst_cannot_decide_approval() -> None:
    repo = FakeApprovalRepository()
    service = ApprovalService(repository=repo)

    with pytest.raises(AuthorizationError):
        await service.decide(
            principal=ANALYST,
            approval_id=APPROVAL_ID,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVED,
            ),
        )


@pytest.mark.asyncio
async def test_admin_cannot_decide_approval() -> None:
    repo = FakeApprovalRepository()
    service = ApprovalService(repository=repo)

    with pytest.raises(AuthorizationError):
        await service.decide(
            principal=ADMIN,
            approval_id=APPROVAL_ID,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVED,
            ),
        )


@pytest.mark.asyncio
async def test_cross_tenant_approval_is_not_found() -> None:
    repo = FakeApprovalRepository()
    service = ApprovalService(repository=repo)

    with pytest.raises(NotFoundError):
        await service.get_approval(
            principal=OTHER_MANAGER,
            approval_id=APPROVAL_ID,
        )

    assert repo.last_get_organization_id == ORG_B


@pytest.mark.asyncio
async def test_manager_decision_uses_server_derived_identity() -> None:
    repo = FakeApprovalRepository()
    service = ApprovalService(repository=repo)

    result = await service.decide(
        principal=MANAGER,
        approval_id=APPROVAL_ID,
        request=ApprovalDecisionRequest(
            decision=ApprovalDecision.APPROVED,
            comment="Reviewed by Manager.",
        ),
    )

    assert result.approver_id == MANAGER_A
    assert result.organization_id == ORG_A
    assert repo.last_decide_organization_id == ORG_A
    assert repo.last_approver_id == MANAGER_A


@pytest.mark.asyncio
async def test_existing_decision_returns_conflict() -> None:
    repo = FakeApprovalRepository()
    repo.pending = None
    repo.decided = _decided()

    service = ApprovalService(repository=repo)

    with pytest.raises(ConflictError):
        await service.decide(
            principal=MANAGER,
            approval_id=APPROVAL_ID,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVED,
            ),
        )


@pytest.mark.asyncio
async def test_cas_loss_returns_conflict_without_retry() -> None:
    repo = FakeApprovalRepository()
    repo.force_cas_loss = True

    service = ApprovalService(repository=repo)

    with pytest.raises(ConflictError):
        await service.decide(
            principal=MANAGER,
            approval_id=APPROVAL_ID,
            request=ApprovalDecisionRequest(
                decision=ApprovalDecision.APPROVED,
            ),
        )


@pytest.mark.asyncio
async def test_missing_approval_returns_not_found() -> None:
    repo = FakeApprovalRepository()
    repo.pending = None

    service = ApprovalService(repository=repo)

    with pytest.raises(NotFoundError):
        await service.get_approval(
            principal=MANAGER,
            approval_id=APPROVAL_ID,
        )
