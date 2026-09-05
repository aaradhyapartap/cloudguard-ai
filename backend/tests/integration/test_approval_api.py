"""Integration tests for the Phase 7.3 human Approval API."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.adapters.local.approval_repository import SQLAlchemyApprovalRepository
from app.adapters.local.identity import LocalIdentityProvider
from app.models.approval import (
    ApprovalAction,
    ApprovalEvidenceReference,
    ApprovalScoreContext,
)
from app.models.enums import ApprovalDecision, ApprovalStatus
from app.models.principal import Principal
from app.repositories.database import dispose_engine, tenant_session, untenanted_session
from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

from conftest import (  # type: ignore[import-not-found]
    bearer,
    skip_without_database,
)

pytestmark = [pytest.mark.integration, skip_without_database]

RESOURCE_ID = UUID("81111111-1111-4111-8111-111111111111")
DOCUMENT_ID = UUID("82222222-2222-4222-8222-222222222222")
CHUNK_ID = UUID("83333333-3333-4333-8333-333333333333")


def _action() -> ApprovalAction:
    return ApprovalAction(
        action_type="escalate_risk",
        resource_type="risk",
        resource_id=RESOURCE_ID,
        summary="Escalate the reviewed risk for Manager-approved follow-up.",
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


async def _create_pending_async(
    *,
    organization_id: UUID,
    task_token: str,
):
    async with tenant_session(organization_id) as session:
        repo = SQLAlchemyApprovalRepository(session)
        return await repo.create_pending(
            organization_id=organization_id,
            workflow_execution_id=f"api-execution-{uuid4()}",
            recommendation_id=uuid4(),
            proposed_action=_action(),
            evidence=_evidence(),
            score_context=_score_context(),
            agent_trace_ids=(
                "research-trace-api",
                "risk-trace-api",
                "reviewer-trace-api",
            ),
            generator_model_id="mock:generator-v1",
            reviewer_model_id="mock:reviewer-v1",
            task_token=task_token,
        )


async def _create_pending_and_dispose(
    *,
    organization_id: UUID,
    task_token: str,
):
    try:
        async with untenanted_session() as session:
            await session.execute(
                sql_text(
                    """
                    INSERT INTO organizations (id, name, slug)
                    VALUES (:id, :name, :slug)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": organization_id,
                    "name": f"Approval API Org {organization_id}",
                    "slug": f"approval-api-{organization_id}",
                },
            )

        return await _create_pending_async(
            organization_id=organization_id,
            task_token=task_token,
        )
    finally:
        await dispose_engine()


def _create_pending(
    *,
    organization_id: UUID,
    task_token: str,
):
    return asyncio.run(
        _create_pending_and_dispose(
            organization_id=organization_id,
            task_token=task_token,
        )
    )


def _manager_headers(
    *,
    token_signer: LocalIdentityProvider,
    manager: Principal,
) -> dict[str, str]:
    return bearer(token_signer, manager)


def test_manager_can_list_and_get_pending_without_task_token(
    client: TestClient,
    token_signer: LocalIdentityProvider,
) -> None:
    organization_id = uuid4()
    manager = Principal(
        user_id=uuid4(),
        organization_id=organization_id,
        role="manager",
        email=f"approval-manager-{uuid4()}@example.test",
        department="Audit",
    )
    secret = f"api-secret-task-token-{uuid4()}"

    pending = _create_pending(
        organization_id=organization_id,
        task_token=secret,
    )

    headers = _manager_headers(
        token_signer=token_signer,
        manager=manager,
    )

    list_response = client.get(
        "/api/v1/approvals",
        headers=headers,
    )
    assert list_response.status_code == 200

    items = list_response.json()
    item = next(row for row in items if row["id"] == str(pending.id))

    assert item["status"] == ApprovalStatus.PENDING.value
    assert "task_token" not in item
    assert secret not in list_response.text

    get_response = client.get(
        f"/api/v1/approvals/{pending.id}",
        headers=headers,
    )
    assert get_response.status_code == 200

    body = get_response.json()
    assert body["id"] == str(pending.id)
    assert body["organization_id"] == str(manager.organization_id)
    assert body["status"] == ApprovalStatus.PENDING.value
    assert "task_token" not in body
    assert secret not in get_response.text


def test_manager_can_approve_and_second_decision_conflicts(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    manager: Principal,
) -> None:
    secret = f"api-secret-task-token-{uuid4()}"

    pending = _create_pending(
        organization_id=manager.organization_id,
        task_token=secret,
    )

    headers = _manager_headers(
        token_signer=token_signer,
        manager=manager,
    )

    response = client.post(
        f"/api/v1/approvals/{pending.id}/decision",
        headers=headers,
        json={
            "decision": ApprovalDecision.APPROVED.value,
            "comment": "Approved after Manager review.",
        },
    )

    assert response.status_code == 200

    body = response.json()
    assert body["id"] == str(pending.id)
    assert body["organization_id"] == str(manager.organization_id)
    assert body["status"] == ApprovalStatus.DECIDED.value
    assert body["decision"] == ApprovalDecision.APPROVED.value
    assert body["approver_id"] == str(manager.user_id)
    assert body["comment"] == "Approved after Manager review."
    assert "task_token" not in body
    assert secret not in response.text

    duplicate = client.post(
        f"/api/v1/approvals/{pending.id}/decision",
        headers=headers,
        json={
            "decision": ApprovalDecision.REJECTED.value,
            "justification": "A later decision must not overwrite the first.",
        },
    )

    assert duplicate.status_code == 409

def test_cross_tenant_approval_is_not_visible_or_decidable(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    manager: Principal,
) -> None:
    foreign_org = UUID("92222222-2222-4222-8222-222222222222")
    secret = f"api-secret-task-token-{uuid4()}"

    pending = _create_pending(
        organization_id=foreign_org,
        task_token=secret,
    )

    headers = _manager_headers(
        token_signer=token_signer,
        manager=manager,
    )

    get_response = client.get(
        f"/api/v1/approvals/{pending.id}",
        headers=headers,
    )
    assert get_response.status_code == 404

    decision_response = client.post(
        f"/api/v1/approvals/{pending.id}/decision",
        headers=headers,
        json={
            "decision": ApprovalDecision.APPROVED.value,
        },
    )
    assert decision_response.status_code == 404


def test_rejected_decision_requires_justification(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    manager: Principal,
) -> None:
    pending = _create_pending(
        organization_id=manager.organization_id,
        task_token=f"api-secret-task-token-{uuid4()}",
    )

    response = client.post(
        f"/api/v1/approvals/{pending.id}/decision",
        headers=_manager_headers(
            token_signer=token_signer,
            manager=manager,
        ),
        json={
            "decision": ApprovalDecision.REJECTED.value,
        },
    )

    assert response.status_code == 422


def test_modified_decision_requires_replacement_action(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    manager: Principal,
) -> None:
    pending = _create_pending(
        organization_id=manager.organization_id,
        task_token=f"api-secret-task-token-{uuid4()}",
    )

    response = client.post(
        f"/api/v1/approvals/{pending.id}/decision",
        headers=_manager_headers(
            token_signer=token_signer,
            manager=manager,
        ),
        json={
            "decision": ApprovalDecision.MODIFIED.value,
            "justification": "Use a narrower action.",
        },
    )

    assert response.status_code == 422


def test_approved_decision_cannot_supply_modified_action(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    manager: Principal,
) -> None:
    pending = _create_pending(
        organization_id=manager.organization_id,
        task_token=f"api-secret-task-token-{uuid4()}",
    )

    response = client.post(
        f"/api/v1/approvals/{pending.id}/decision",
        headers=_manager_headers(
            token_signer=token_signer,
            manager=manager,
        ),
        json={
            "decision": ApprovalDecision.APPROVED.value,
            "modified_action": {
                "action_type": "escalate_risk",
                "resource_type": "risk",
                "resource_id": str(RESOURCE_ID),
                "summary": "Attempted unauthorized replacement.",
            },
        },
    )

    assert response.status_code == 422

def test_decision_body_rejects_server_owned_identity_and_token_fields(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    manager: Principal,
) -> None:
    pending = _create_pending(
        organization_id=manager.organization_id,
        task_token=f"api-secret-task-token-{uuid4()}",
    )

    response = client.post(
        f"/api/v1/approvals/{pending.id}/decision",
        headers=_manager_headers(
            token_signer=token_signer,
            manager=manager,
        ),
        json={
            "decision": ApprovalDecision.APPROVED.value,
            "organization_id": str(UUID("99999999-9999-4999-8999-999999999999")),
            "approver_id": str(UUID("98888888-8888-4888-8888-888888888888")),
            "task_token": "attacker-controlled-task-token",
        },
    )

    assert response.status_code == 422

def test_manager_can_reject_with_required_justification(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    manager: Principal,
) -> None:
    pending = _create_pending(
        organization_id=manager.organization_id,
        task_token=f"api-secret-task-token-{uuid4()}",
    )

    response = client.post(
        f"/api/v1/approvals/{pending.id}/decision",
        headers=_manager_headers(
            token_signer=token_signer,
            manager=manager,
        ),
        json={
            "decision": ApprovalDecision.REJECTED.value,
            "justification": "Evidence does not support executing the proposed action.",
        },
    )

    assert response.status_code == 200

    body = response.json()
    assert body["id"] == str(pending.id)
    assert body["status"] == ApprovalStatus.DECIDED.value
    assert body["decision"] == ApprovalDecision.REJECTED.value
    assert body["approver_id"] == str(manager.user_id)
    assert body["justification"] == (
        "Evidence does not support executing the proposed action."
    )
    assert body["modified_action"] is None
    assert "task_token" not in body


def test_manager_can_modify_with_bounded_replacement_action(
    client: TestClient,
    token_signer: LocalIdentityProvider,
    manager: Principal,
) -> None:
    pending = _create_pending(
        organization_id=manager.organization_id,
        task_token=f"api-secret-task-token-{uuid4()}",
    )

    replacement = {
        "action_type": "escalate_risk",
        "resource_type": "risk",
        "resource_id": str(RESOURCE_ID),
        "summary": "Escalate only for documented Manager review.",
    }

    response = client.post(
        f"/api/v1/approvals/{pending.id}/decision",
        headers=_manager_headers(
            token_signer=token_signer,
            manager=manager,
        ),
        json={
            "decision": ApprovalDecision.MODIFIED.value,
            "justification": "Narrow the recommended action before execution.",
            "modified_action": replacement,
        },
    )

    assert response.status_code == 200

    body = response.json()
    assert body["id"] == str(pending.id)
    assert body["status"] == ApprovalStatus.DECIDED.value
    assert body["decision"] == ApprovalDecision.MODIFIED.value
    assert body["approver_id"] == str(manager.user_id)
    assert body["justification"] == (
        "Narrow the recommended action before execution."
    )
    assert body["modified_action"] == replacement
    assert "task_token" not in body
