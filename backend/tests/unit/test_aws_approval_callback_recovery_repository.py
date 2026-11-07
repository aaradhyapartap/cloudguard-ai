"""Tests for Aurora Data API Approval callback recovery persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.adapters.aws.approval_callback_recovery_repository import (
    AuroraDataAPIApprovalCallbackRecoveryRepository,
)
from app.models.enums import ApprovalDecision

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
APPROVAL_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RESOURCE_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CLAIM_ID_TEXT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
STALE_BEFORE = datetime(2026, 9, 6, 7, 0, tzinfo=UTC)


class RecordingRDSDataClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def begin_transaction(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(("begin_transaction", kwargs))
        return {"transactionId": "transaction-1"}

    def execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("execute_statement", kwargs))
        sql = kwargs["sql"]

        if "FROM organizations" in sql:
            return {
                "records": [
                    [{"stringValue": str(ORG_ID)}],
                ]
            }

        if (
            "SELECT id::text" in sql
            and "FROM approvals" in sql
        ):
            return {
                "records": [
                    [{"stringValue": str(APPROVAL_ID)}],
                ]
            }

        if (
            "UPDATE approvals" in sql
            and "RETURNING id::text, organization_id::text" in sql
        ):
            parameters = {
                parameter["name"]: parameter
                for parameter in kwargs["parameters"]
            }

            return {
                "records": [
                    [
                        {"stringValue": str(APPROVAL_ID)},
                        {"stringValue": str(ORG_ID)},
                        {"stringValue": "server-owned-task-token"},
                        {"stringValue": "approved"},
                        {
                            "stringValue": (
                                '{"action_type":"create_security_review",'
                                '"resource_type":"workflow_execution",'
                                f'"resource_id":"{RESOURCE_ID}",'
                                '"summary":"Create a bounded security review."}'
                            )
                        },
                        {"isNull": True},
                    ]
                ],
                "_claim_parameter": parameters["claim_id"],
            }

        if (
            "UPDATE approvals" in sql
            and "callback_delivered_at = now()" in sql
        ):
            return {
                "records": [
                    [{"stringValue": str(APPROVAL_ID)}],
                ]
            }

        if (
            "UPDATE approvals" in sql
            and "callback_claim_id = NULL" in sql
        ):
            return {
                "records": [
                    [{"stringValue": str(APPROVAL_ID)}],
                ]
            }

        return {}

    def commit_transaction(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("commit_transaction", kwargs))
        return {}

    def rollback_transaction(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("rollback_transaction", kwargs))
        return {}


def _repository(
    client: RecordingRDSDataClient,
) -> AuroraDataAPIApprovalCallbackRecoveryRepository:
    return AuroraDataAPIApprovalCallbackRecoveryRepository(
        resource_arn="arn:aws:rds:us-east-1:123456789012:cluster:test",
        secret_arn=(
            "arn:aws:secretsmanager:us-east-1:"
            "123456789012:secret:test"
        ),
        database="cloudguard",
        region="us-east-1",
        client=client,
    )


@pytest.mark.asyncio
async def test_lists_organizations_without_tenant_token() -> None:
    client = RecordingRDSDataClient()
    repository = _repository(client)

    organizations = await repository.list_organization_ids(
        after_id=None
    )

    assert organizations == (ORG_ID,)

    statement_calls = [
        kwargs
        for name, kwargs in client.calls
        if name == "execute_statement"
    ]

    assert len(statement_calls) == 1
    assert "FROM organizations" in statement_calls[0]["sql"]
    assert "transactionId" not in statement_calls[0]


@pytest.mark.asyncio
async def test_lists_recoverable_ids_inside_tenant_transaction() -> None:
    client = RecordingRDSDataClient()
    repository = _repository(client)

    approval_ids = await repository.list_recoverable_approval_ids(
        organization_id=ORG_ID,
        stale_before=STALE_BEFORE,
    )

    assert approval_ids == (APPROVAL_ID,)

    assert [name for name, _ in client.calls] == [
        "begin_transaction",
        "execute_statement",
        "execute_statement",
        "commit_transaction",
    ]

    tenant_call = client.calls[1][1]
    assert "set_config" in tenant_call["sql"]
    assert tenant_call["transactionId"] == "transaction-1"

    recovery_call = client.calls[2][1]
    assert recovery_call["transactionId"] == "transaction-1"
    assert "callback_delivered_at IS NULL" in recovery_call["sql"]
    assert "callback_claimed_at IS NULL" in recovery_call["sql"]
    assert "callback_claimed_at <" in recovery_call["sql"]


@pytest.mark.asyncio
async def test_claim_returns_internal_token_and_effective_action() -> None:
    client = RecordingRDSDataClient()
    repository = _repository(client)

    claim = await repository.claim_decided_callback(
        organization_id=ORG_ID,
        approval_id=APPROVAL_ID,
        stale_before=STALE_BEFORE,
    )

    assert claim is not None
    assert claim.organization_id == ORG_ID
    assert claim.approval_id == APPROVAL_ID
    assert claim.task_token == "server-owned-task-token"
    assert claim.decision is ApprovalDecision.APPROVED
    assert claim.effective_action is not None
    assert (
        claim.effective_action.action_type
        == "create_security_review"
    )

    update_call = client.calls[2][1]
    assert "callback_attempt_count = callback_attempt_count + 1" in (
        update_call["sql"]
    )
    assert "callback_claim_id = CAST(:claim_id AS uuid)" in (
        update_call["sql"]
    )


@pytest.mark.asyncio
async def test_mark_delivery_requires_owned_claim() -> None:
    client = RecordingRDSDataClient()
    repository = _repository(client)

    claim = await repository.claim_decided_callback(
        organization_id=ORG_ID,
        approval_id=APPROVAL_ID,
        stale_before=STALE_BEFORE,
    )

    assert claim is not None

    delivered = await repository.mark_callback_delivered(
        organization_id=ORG_ID,
        approval_id=APPROVAL_ID,
        claim_id=claim.claim_id,
    )

    assert delivered is True

    delivery_calls = [
        kwargs
        for name, kwargs in client.calls
        if (
            name == "execute_statement"
            and "callback_delivered_at = now()"
            in kwargs["sql"]
        )
    ]

    assert len(delivery_calls) == 1
    assert (
        "callback_claim_id = CAST(:claim_id AS uuid)"
        in delivery_calls[0]["sql"]
    )


@pytest.mark.asyncio
async def test_release_requires_owned_claim_and_preserves_attempts() -> None:
    client = RecordingRDSDataClient()
    repository = _repository(client)

    claim = await repository.claim_decided_callback(
        organization_id=ORG_ID,
        approval_id=APPROVAL_ID,
        stale_before=STALE_BEFORE,
    )

    assert claim is not None

    released = await repository.release_callback_claim(
        organization_id=ORG_ID,
        approval_id=APPROVAL_ID,
        claim_id=claim.claim_id,
    )

    assert released is True

    release_calls = [
        kwargs
        for name, kwargs in client.calls
        if (
            name == "execute_statement"
            and "callback_claim_id = NULL"
            in kwargs["sql"]
            and "callback_delivered_at = now()"
            not in kwargs["sql"]
        )
    ]

    assert len(release_calls) == 1
    assert "callback_attempt_count =" not in release_calls[0]["sql"]
    assert (
        "callback_claim_id = CAST(:claim_id AS uuid)"
        in release_calls[0]["sql"]
    )
