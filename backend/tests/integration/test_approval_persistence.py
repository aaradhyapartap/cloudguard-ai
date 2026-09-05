"""Integration tests for Phase 7 human Approval persistence.

Run with PostgreSQL and migration head applied, using the unprivileged
``cloudguard_app`` role so Row-Level Security is exercised (consistent
with .github/workflows/ci.yml):

    docker compose up -d postgres
    cd backend
    python -m alembic upgrade head
    $env:RUN_DB_TESTS = "1"
    $env:DB_USER = "cloudguard_app"
    $env:DB_PASSWORD = "cloudguard_app"
    python -m pytest tests/integration/test_approval_persistence.py -q
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.adapters.local.approval_repository import (
    SQLAlchemyApprovalRepository,
)
from app.models.approval import (
    ApprovalAction,
    ApprovalEvidenceReference,
    ApprovalScoreContext,
    DecidedApproval,
    PendingApproval,
)
from app.models.enums import ApprovalDecision, ApprovalStatus
from app.repositories.database import tenant_session, untenanted_session
from app.repositories.tables import Approval, Organization, User
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

pytestmark = pytest.mark.integration

ORG_A = UUID("71111111-1111-4111-8111-111111111111")
ORG_B = UUID("72222222-2222-4222-8222-222222222222")

MANAGER_A = UUID("73333333-3333-4333-8333-333333333333")
MANAGER_B = UUID("74444444-4444-4444-8444-444444444444")

RESOURCE_ID = UUID("75555555-5555-4555-8555-555555555555")
DOCUMENT_ID = UUID("76666666-6666-4666-8666-666666666666")
CHUNK_ID = UUID("77777777-7777-4777-8777-777777777777")


def _skip_without_database() -> None:
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 with PostgreSQL running")


def _action(
    *,
    summary: str = "Escalate the reviewed risk for Manager-approved follow-up.",
) -> ApprovalAction:
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


async def _create_pending(
    *,
    organization_id: UUID,
    workflow_execution_id: str | None = None,
    task_token: str | None = None,
) -> PendingApproval:
    async with tenant_session(organization_id) as session:
        repo = SQLAlchemyApprovalRepository(session)

        return await repo.create_pending(
            organization_id=organization_id,
            workflow_execution_id=(
                workflow_execution_id
                or f"execution-{uuid4()}"
            ),
            recommendation_id=uuid4(),
            proposed_action=_action(),
            evidence=_evidence(),
            score_context=_score_context(),
            agent_trace_ids=(
                "research-trace-1",
                "risk-trace-1",
                "reviewer-trace-1",
            ),
            generator_model_id="mock:generator-v1",
            reviewer_model_id="mock:reviewer-v1",
            task_token=task_token or f"secret-task-token-{uuid4()}",
        )


@pytest.fixture(scope="module", autouse=True)
async def seed_approval_test_data() -> object:
    _skip_without_database()

    # Organizations are global catalog rows and intentionally have no RLS.
    async with untenanted_session() as session:
        for org_id, name, slug in (
            (ORG_A, "Approval Integration Org A", "approval-integration-a"),
            (ORG_B, "Approval Integration Org B", "approval-integration-b"),
        ):
            existing = await session.execute(
                select(Organization).where(Organization.id == org_id)
            )
            if existing.scalar_one_or_none() is None:
                session.add(
                    Organization(
                        id=org_id,
                        name=name,
                        slug=slug,
                    )
                )

    # Users are tenant-owned and therefore must be created inside tenant scope.
    async with tenant_session(ORG_A) as session:
        existing = await session.execute(
            select(User).where(User.id == MANAGER_A)
        )
        if existing.scalar_one_or_none() is None:
            await session.execute(
                text(
                    """
                    INSERT INTO users (
                        id,
                        organization_id,
                        email,
                        role,
                        department,
                        last_login_at
                    )
                    VALUES (
                        :id,
                        :organization_id,
                        :email,
                        CAST('manager' AS role),
                        'Audit',
                        now()
                    )
                    """
                ),
                {
                    "id": MANAGER_A,
                    "organization_id": ORG_A,
                    "email": "approval-manager-a@integration.test",
                },
            )

    async with tenant_session(ORG_B) as session:
        existing = await session.execute(
            select(User).where(User.id == MANAGER_B)
        )
        if existing.scalar_one_or_none() is None:
            await session.execute(
                text(
                    """
                    INSERT INTO users (
                        id,
                        organization_id,
                        email,
                        role,
                        department,
                        last_login_at
                    )
                    VALUES (
                        :id,
                        :organization_id,
                        :email,
                        CAST('manager' AS role),
                        'Audit',
                        now()
                    )
                    """
                ),
                {
                    "id": MANAGER_B,
                    "organization_id": ORG_B,
                    "email": "approval-manager-b@integration.test",
                },
            )

    yield


async def test_create_pending_round_trips_public_snapshot() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    assert pending.organization_id == ORG_A
    assert pending.status is ApprovalStatus.PENDING
    assert pending.evidence == _evidence()
    assert pending.score_context == _score_context()

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)
        fetched = await repo.get_by_id(
            organization_id=ORG_A,
            approval_id=pending.id,
        )

    assert isinstance(fetched, PendingApproval)
    assert fetched == pending


async def test_public_repository_reads_do_not_expose_task_token() -> None:
    secret = f"secret-task-token-{uuid4()}"

    pending = await _create_pending(
        organization_id=ORG_A,
        task_token=secret,
    )

    assert not hasattr(pending, "task_token")
    assert "task_token" not in pending.model_dump()

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)
        fetched = await repo.get_by_id(
            organization_id=ORG_A,
            approval_id=pending.id,
        )

    assert fetched is not None
    assert not hasattr(fetched, "task_token")
    assert "task_token" not in fetched.model_dump()


async def test_callback_context_is_hidden_while_pending() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)
        context = await repo.get_decided_callback_context(
            organization_id=ORG_A,
            approval_id=pending.id,
        )

    assert context is None


async def test_pending_queue_is_tenant_scoped() -> None:
    approval_a = await _create_pending(
        organization_id=ORG_A,
    )
    approval_b = await _create_pending(
        organization_id=ORG_B,
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)
        queue_a = await repo.list_pending(
            organization_id=ORG_A,
            limit=100,
        )

    async with tenant_session(ORG_B) as session:
        repo = SQLAlchemyApprovalRepository(session)
        queue_b = await repo.list_pending(
            organization_id=ORG_B,
            limit=100,
        )

    ids_a = {approval.id for approval in queue_a}
    ids_b = {approval.id for approval in queue_b}

    assert approval_a.id in ids_a
    assert approval_b.id not in ids_a

    assert approval_b.id in ids_b
    assert approval_a.id not in ids_b


async def test_rls_blocks_bare_cross_tenant_approval_select() -> None:
    approval_a = await _create_pending(
        organization_id=ORG_A,
    )
    approval_b = await _create_pending(
        organization_id=ORG_B,
    )

    # Deliberately omit organization_id from the query.
    async with tenant_session(ORG_A) as session:
        result = await session.execute(
            select(Approval).where(
                Approval.id.in_(
                    (approval_a.id, approval_b.id)
                )
            )
        )
        visible = result.scalars().all()

    assert {row.id for row in visible} == {approval_a.id}


async def test_decide_pending_is_atomic_first_decision_wins() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )
    decided_at = datetime.now(UTC)

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        first = await repo.decide_pending(
            organization_id=ORG_A,
            approval_id=pending.id,
            decision=ApprovalDecision.APPROVED,
            approver_id=MANAGER_A,
            decided_at=decided_at,
            justification=None,
            comment="Approved after evidence review.",
            modified_action=None,
        )

    assert isinstance(first, DecidedApproval)
    assert first.decision is ApprovalDecision.APPROVED

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        second = await repo.decide_pending(
            organization_id=ORG_A,
            approval_id=pending.id,
            decision=ApprovalDecision.REJECTED,
            approver_id=MANAGER_A,
            decided_at=datetime.now(UTC),
            justification="Second decision must not overwrite the first.",
            comment=None,
            modified_action=None,
        )

    assert second is None

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)
        fetched = await repo.get_by_id(
            organization_id=ORG_A,
            approval_id=pending.id,
        )

    assert isinstance(fetched, DecidedApproval)
    assert fetched.decision is ApprovalDecision.APPROVED
    assert fetched.approver_id == MANAGER_A
    assert fetched.decided_at == decided_at


async def test_callback_context_returns_secret_only_after_decision() -> None:
    secret = f"secret-task-token-{uuid4()}"

    pending = await _create_pending(
        organization_id=ORG_A,
        task_token=secret,
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        decided = await repo.decide_pending(
            organization_id=ORG_A,
            approval_id=pending.id,
            decision=ApprovalDecision.APPROVED,
            approver_id=MANAGER_A,
            decided_at=datetime.now(UTC),
            justification=None,
            comment=None,
            modified_action=None,
        )

        assert decided is not None

        context = await repo.get_decided_callback_context(
            organization_id=ORG_A,
            approval_id=pending.id,
        )

    assert context is not None
    assert context.task_token == secret
    assert context.decision is ApprovalDecision.APPROVED
    assert context.effective_action == _action()


async def test_modified_callback_uses_only_human_replacement_action() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    modified_action = _action(
        summary="Escalate only the Manager-bounded replacement action.",
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        decided = await repo.decide_pending(
            organization_id=ORG_A,
            approval_id=pending.id,
            decision=ApprovalDecision.MODIFIED,
            approver_id=MANAGER_A,
            decided_at=datetime.now(UTC),
            justification="Manager narrowed the original recommendation.",
            comment=None,
            modified_action=modified_action,
        )

        assert decided is not None

        context = await repo.get_decided_callback_context(
            organization_id=ORG_A,
            approval_id=pending.id,
        )

    assert context is not None
    assert context.decision is ApprovalDecision.MODIFIED
    assert context.effective_action == modified_action
    assert context.effective_action != pending.proposed_action


async def test_rejected_callback_has_no_effective_action() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        decided = await repo.decide_pending(
            organization_id=ORG_A,
            approval_id=pending.id,
            decision=ApprovalDecision.REJECTED,
            approver_id=MANAGER_A,
            decided_at=datetime.now(UTC),
            justification="Manager rejected the proposed consequential action.",
            comment=None,
            modified_action=None,
        )

        assert decided is not None

        context = await repo.get_decided_callback_context(
            organization_id=ORG_A,
            approval_id=pending.id,
        )

    assert context is not None
    assert context.decision is ApprovalDecision.REJECTED
    assert context.effective_action is None


async def test_cross_tenant_manager_cannot_be_persisted_as_approver() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        with pytest.raises(IntegrityError):
            await repo.decide_pending(
                organization_id=ORG_A,
                approval_id=pending.id,
                decision=ApprovalDecision.APPROVED,
                approver_id=MANAGER_B,
                decided_at=datetime.now(UTC),
                justification=None,
                comment=None,
                modified_action=None,
            )


async def test_frozen_recommendation_context_trigger_rejects_update() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    async with tenant_session(ORG_A) as session:
        with pytest.raises(
            (DBAPIError, ProgrammingError, IntegrityError)
        ) as exc_info:
            await session.execute(
                text(
                    """
                    UPDATE approvals
                    SET proposed_action = jsonb_build_object(
                        'action_type', 'tampered',
                        'resource_type', 'risk',
                        'resource_id', CAST(:resource_id AS text),
                        'summary', 'tampered recommendation'
                    )
                    WHERE id = :approval_id
                    """
                ),
                {
                    "resource_id": str(RESOURCE_ID),
                    "approval_id": pending.id,
                },
            )

        assert (
            "approval recommendation context is immutable"
            in str(exc_info.value)
            or "permission denied" in str(exc_info.value)
        )


async def test_approval_delete_is_rejected() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    async with tenant_session(ORG_A) as session:
        with pytest.raises(
            (DBAPIError, ProgrammingError, IntegrityError)
        ) as exc_info:
            await session.execute(
                text(
                    "DELETE FROM approvals WHERE id = :approval_id"
                ),
                {"approval_id": pending.id},
            )

        assert (
            "approval rows cannot be deleted" in str(exc_info.value)
            or "permission denied" in str(exc_info.value)
        )


async def test_decision_metadata_is_immutable_after_decision() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        decided = await repo.decide_pending(
            organization_id=ORG_A,
            approval_id=pending.id,
            decision=ApprovalDecision.APPROVED,
            approver_id=MANAGER_A,
            decided_at=datetime.now(UTC),
            justification=None,
            comment="Original human comment.",
            modified_action=None,
        )

        assert decided is not None

    async with tenant_session(ORG_A) as session:
        with pytest.raises(
            (DBAPIError, ProgrammingError, IntegrityError)
        ) as exc_info:
            await session.execute(
                text(
                    """
                    UPDATE approvals
                    SET comment = 'tampered comment'
                    WHERE id = :approval_id
                    """
                ),
                {"approval_id": pending.id},
            )

        assert (
            "approval human decision is immutable"
            in str(exc_info.value)
            or "permission denied" in str(exc_info.value)
        )


async def test_decided_may_advance_to_execution_succeeded() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        decided = await repo.decide_pending(
            organization_id=ORG_A,
            approval_id=pending.id,
            decision=ApprovalDecision.APPROVED,
            approver_id=MANAGER_A,
            decided_at=datetime.now(UTC),
            justification=None,
            comment=None,
            modified_action=None,
        )

        assert decided is not None

    async with tenant_session(ORG_A) as session:
        await session.execute(
            text(
                """
                UPDATE approvals
                SET status = 'execution_succeeded'
                WHERE id = :approval_id
                """
            ),
            {"approval_id": pending.id},
        )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)
        fetched = await repo.get_by_id(
            organization_id=ORG_A,
            approval_id=pending.id,
        )

    assert isinstance(fetched, DecidedApproval)
    assert fetched.status is ApprovalStatus.EXECUTION_SUCCEEDED


async def test_terminal_approval_does_not_return_callback_context() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        decided = await repo.decide_pending(
            organization_id=ORG_A,
            approval_id=pending.id,
            decision=ApprovalDecision.APPROVED,
            approver_id=MANAGER_A,
            decided_at=datetime.now(UTC),
            justification=None,
            comment=None,
            modified_action=None,
        )

        assert decided is not None

    async with tenant_session(ORG_A) as session:
        await session.execute(
            text(
                """
                UPDATE approvals
                SET status = 'execution_succeeded'
                WHERE id = :approval_id
                """
            ),
            {"approval_id": pending.id},
        )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        context = await repo.get_decided_callback_context(
            organization_id=ORG_A,
            approval_id=pending.id,
        )

    assert context is None


async def test_terminal_execution_state_rejects_further_mutation() -> None:
    pending = await _create_pending(
        organization_id=ORG_A,
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyApprovalRepository(session)

        decided = await repo.decide_pending(
            organization_id=ORG_A,
            approval_id=pending.id,
            decision=ApprovalDecision.APPROVED,
            approver_id=MANAGER_A,
            decided_at=datetime.now(UTC),
            justification=None,
            comment=None,
            modified_action=None,
        )

        assert decided is not None

    async with tenant_session(ORG_A) as session:
        await session.execute(
            text(
                """
                UPDATE approvals
                SET status = 'execution_failed'
                WHERE id = :approval_id
                """
            ),
            {"approval_id": pending.id},
        )

    async with tenant_session(ORG_A) as session:
        with pytest.raises(
            (DBAPIError, ProgrammingError, IntegrityError)
        ) as exc_info:
            await session.execute(
                text(
                    """
                    UPDATE approvals
                    SET status = 'execution_succeeded'
                    WHERE id = :approval_id
                    """
                ),
                {"approval_id": pending.id},
            )

        assert "terminal approval state is immutable" in str(
            exc_info.value
        )


async def test_task_token_is_unique() -> None:
    shared_token = f"shared-secret-task-token-{uuid4()}"

    await _create_pending(
        organization_id=ORG_A,
        task_token=shared_token,
    )

    with pytest.raises(IntegrityError):
        await _create_pending(
            organization_id=ORG_B,
            task_token=shared_token,
        )


async def test_workflow_execution_is_unique_within_tenant() -> None:
    workflow_execution_id = f"workflow-{uuid4()}"

    await _create_pending(
        organization_id=ORG_A,
        workflow_execution_id=workflow_execution_id,
    )

    with pytest.raises(IntegrityError):
        await _create_pending(
            organization_id=ORG_A,
            workflow_execution_id=workflow_execution_id,
        )

    # The same external workflow identifier is allowed in a different tenant.
    other = await _create_pending(
        organization_id=ORG_B,
        workflow_execution_id=workflow_execution_id,
    )

    assert other.organization_id == ORG_B
