"""Tests for Aurora Data API Approval creation persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from app.adapters.aws.approval_creation_repository import (
    AuroraDataAPIApprovalCreationRepository,
)
from app.core.errors import UpstreamError
from app.models.approval import ApprovalAction, ApprovalEvidenceReference

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
EXECUTION_ID = UUID("22222222-2222-4222-8222-222222222222")
RECOMMENDATION_ID = UUID("33333333-3333-4333-8333-333333333333")
CHUNK_ID = UUID("44444444-4444-4444-8444-444444444444")
DOCUMENT_ID = UUID("55555555-5555-4555-8555-555555555555")


class RecordingRDSDataClient:
    def __init__(self, *, fail_insert: bool = False) -> None:
        self.fail_insert = fail_insert
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def begin_transaction(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(("begin_transaction", kwargs))
        return {"transactionId": "transaction-1"}

    def execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("execute_statement", kwargs))

        if "INSERT INTO approvals" in kwargs["sql"]:
            if self.fail_insert:
                raise RuntimeError("simulated Data API failure")

            parameters = {
                parameter["name"]: parameter
                for parameter in kwargs["parameters"]
            }
            approval_id = parameters["id"]["value"]["stringValue"]

            return {
                "records": [
                    [
                        {"stringValue": approval_id},
                        {"stringValue": "2026-09-05T20:30:00+00:00"},
                    ]
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
) -> AuroraDataAPIApprovalCreationRepository:
    return AuroraDataAPIApprovalCreationRepository(
        resource_arn="arn:aws:rds:us-east-1:123456789012:cluster:test",
        secret_arn=(
            "arn:aws:secretsmanager:us-east-1:"
            "123456789012:secret:test"
        ),
        database="cloudguard",
        region="us-east-1",
        client=client,
    )


async def _create(
    repository: AuroraDataAPIApprovalCreationRepository,
):
    return await repository.create_pending(
        organization_id=ORG_ID,
        workflow_execution_id=str(EXECUTION_ID),
        recommendation_id=RECOMMENDATION_ID,
        proposed_action=ApprovalAction(
            action_type="create_security_review",
            resource_type="workflow_execution",
            resource_id=EXECUTION_ID,
            summary="Create a bounded security review.",
        ),
        evidence=(
            ApprovalEvidenceReference(
                chunk_id=CHUNK_ID,
                document_id=DOCUMENT_ID,
            ),
        ),
        score_context=None,
        agent_trace_ids=(),
        generator_model_id=None,
        reviewer_model_id="mock:reviewer-v1",
        task_token="internal-task-token",
    )


@pytest.mark.asyncio
async def test_create_pending_sets_tenant_and_commits() -> None:
    client = RecordingRDSDataClient()
    repository = _repository(client)

    pending = await _create(repository)

    assert [name for name, _ in client.calls] == [
        "begin_transaction",
        "execute_statement",
        "execute_statement",
        "commit_transaction",
    ]

    tenant_call = client.calls[1][1]
    assert "set_config" in tenant_call["sql"]
    assert tenant_call["transactionId"] == "transaction-1"

    insert_call = client.calls[2][1]
    assert insert_call["transactionId"] == "transaction-1"

    parameters = {
        parameter["name"]: parameter
        for parameter in insert_call["parameters"]
    }

    assert (
        parameters["organization_id"]["value"]["stringValue"]
        == str(ORG_ID)
    )
    assert (
        parameters["task_token"]["value"]["stringValue"]
        == "internal-task-token"
    )

    assert pending.organization_id == ORG_ID
    assert pending.workflow_execution_id == str(EXECUTION_ID)
    assert pending.recommendation_id == RECOMMENDATION_ID
    assert not hasattr(pending, "task_token")
    assert "task_token" not in pending.model_dump()


@pytest.mark.asyncio
async def test_create_pending_sql_allows_only_exact_pending_retry() -> None:
    client = RecordingRDSDataClient()
    repository = _repository(client)

    await _create(repository)

    insert_call = client.calls[2][1]
    sql = insert_call["sql"]

    assert (
        "ON CONFLICT (organization_id, workflow_execution_id)"
        in sql
    )
    assert "approvals.status = CAST('pending' AS approval_status)" in sql
    assert "approvals.decision IS NULL" in sql

    assert (
        "approvals.recommendation_id = EXCLUDED.recommendation_id"
        in sql
    )
    assert "approvals.proposed_action = EXCLUDED.proposed_action" in sql
    assert "approvals.evidence = EXCLUDED.evidence" in sql
    assert (
        "approvals.score_context "
        "IS NOT DISTINCT FROM EXCLUDED.score_context"
        in sql
    )
    assert (
        "approvals.agent_trace_ids = EXCLUDED.agent_trace_ids"
        in sql
    )
    assert (
        "approvals.generator_model_id "
        "IS NOT DISTINCT FROM EXCLUDED.generator_model_id"
        in sql
    )
    assert (
        "approvals.reviewer_model_id "
        "IS NOT DISTINCT FROM EXCLUDED.reviewer_model_id"
        in sql
    )
    assert "approvals.task_token = EXCLUDED.task_token" in sql


@pytest.mark.asyncio
async def test_create_pending_rolls_back_when_insert_fails() -> None:
    client = RecordingRDSDataClient(fail_insert=True)
    repository = _repository(client)

    with pytest.raises(UpstreamError):
        await _create(repository)

    assert [name for name, _ in client.calls] == [
        "begin_transaction",
        "execute_statement",
        "execute_statement",
        "rollback_transaction",
    ]
