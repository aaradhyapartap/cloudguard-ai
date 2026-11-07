"""Tests for the atomic Aurora approved-action execution repository."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from app.adapters.aws.approval_execution_repository import (
    AuroraDataAPIApprovalExecutionRepository,
    CallbackDeliveryNotCommittedError,
)
from app.core.errors import UpstreamError
from app.models.approved_action_execution import ActionExecutionDisposition
from app.models.enums import ApprovalDecision, ApprovalStatus

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
EXECUTION_ID = UUID("22222222-2222-4222-8222-222222222222")
APPROVAL_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RECOMMENDATION_ID = UUID("33333333-3333-4333-8333-333333333333")
APPROVER_ID = UUID("44444444-4444-4444-8444-444444444444")

CREATED_AT = "2026-09-06T08:00:00+00:00"
DECIDED_AT = "2026-09-06T08:05:00+00:00"
CALLBACK_AT = "2026-09-06T08:06:00+00:00"
COMPLETED_AT = "2026-09-06T08:07:00+00:00"


def _field(value: str | None) -> dict[str, object]:
    if value is None:
        return {"isNull": True}
    return {"stringValue": value}


def _action(
    *,
    summary: str,
    action_type: str = "create_security_review",
) -> dict[str, object]:
    return {
        "action_type": action_type,
        "resource_type": "workflow_execution",
        "resource_id": str(EXECUTION_ID),
        "summary": summary,
    }


class RecordingRDSDataClient:
    def __init__(
        self,
        *,
        status: ApprovalStatus = ApprovalStatus.DECIDED,
        decision: ApprovalDecision = ApprovalDecision.APPROVED,
        proposed_action: dict[str, object] | None = None,
        modified_action: dict[str, object] | None = None,
        callback_delivered: bool = True,
        stored_idempotency_key: str | None = None,
        stored_reference: str | None = None,
        stored_error_code: str | None = None,
        fail_sql_contains: str | None = None,
    ) -> None:
        self.calls: list[
            tuple[str, dict[str, Any]]
        ] = []

        self.status = status
        self.decision = decision
        self.proposed_action = (
            proposed_action
            or _action(summary="Original human-approved action.")
        )
        self.modified_action = modified_action
        self.callback_delivered = callback_delivered
        self.stored_idempotency_key = stored_idempotency_key
        self.stored_reference = stored_reference
        self.stored_error_code = stored_error_code
        self.fail_sql_contains = fail_sql_contains

    def begin_transaction(
        self,
        **kwargs: Any,
    ) -> dict[str, str]:
        self.calls.append(
            ("begin_transaction", kwargs)
        )
        return {
            "transactionId": "transaction-1",
        }

    def _approval_record(
        self,
    ) -> list[dict[str, object]]:
        justification = None

        if self.decision is ApprovalDecision.MODIFIED:
            justification = "Manager supplied replacement action."
        elif self.decision is ApprovalDecision.REJECTED:
            justification = "Manager rejected action."

        return [
            _field(str(APPROVAL_ID)),
            _field(str(ORG_ID)),
            _field(str(EXECUTION_ID)),
            _field(str(RECOMMENDATION_ID)),
            _field(
                json.dumps(
                    self.proposed_action,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            ),
            _field("[]"),
            _field(None),
            _field("[]"),
            _field(None),
            _field("mock:reviewer"),
            _field(self.status.value),
            _field(CREATED_AT),
            _field(self.decision.value),
            _field(str(APPROVER_ID)),
            _field(DECIDED_AT),
            _field(justification),
            _field(None),
            _field(
                None
                if self.modified_action is None
                else json.dumps(
                    self.modified_action,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            ),
            _field(
                CALLBACK_AT
                if self.callback_delivered
                else None
            ),
            _field(self.stored_idempotency_key),
            _field(
                COMPLETED_AT
                if self.status
                in {
                    ApprovalStatus.EXECUTION_SUCCEEDED,
                    ApprovalStatus.EXECUTION_FAILED,
                }
                else None
            ),
            _field(self.stored_reference),
            _field(self.stored_error_code),
        ]

    def execute_statement(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            ("execute_statement", kwargs)
        )

        sql = str(kwargs["sql"])

        if (
            self.fail_sql_contains is not None
            and self.fail_sql_contains in sql
        ):
            raise RuntimeError(
                "provider-secret-details-must-not-escape"
            )

        if sql.startswith("SELECT set_config"):
            return {}

        if (
            "FROM approvals" in sql
            and "FOR UPDATE" in sql
        ):
            return {
                "records": [
                    self._approval_record()
                ]
            }

        if "INSERT INTO security_reviews" in sql:
            return {
                "records": [
                    [
                        {
                            "stringValue": (
                                "55555555-5555-4555-8555-555555555555"
                            )
                        }
                    ]
                ]
            }

        if (
            "UPDATE approvals" in sql
            and "execution_succeeded" in sql
        ):
            return {
                "records": [
                    [
                        {
                            "stringValue": str(
                                APPROVAL_ID
                            )
                        }
                    ]
                ]
            }

        if (
            "UPDATE approvals" in sql
            and "execution_failed" in sql
        ):
            return {
                "records": [
                    [
                        {
                            "stringValue": str(
                                APPROVAL_ID
                            )
                        }
                    ]
                ]
            }

        if "FROM security_reviews" in sql:
            raise AssertionError(
                "Exact-retry reconciliation was not expected."
            )

        return {}

    def commit_transaction(
        self,
        **kwargs: Any,
    ) -> dict[str, object]:
        self.calls.append(
            ("commit_transaction", kwargs)
        )
        return {}

    def rollback_transaction(
        self,
        **kwargs: Any,
    ) -> dict[str, object]:
        self.calls.append(
            ("rollback_transaction", kwargs)
        )
        return {}


def _repository(
    client: RecordingRDSDataClient,
) -> AuroraDataAPIApprovalExecutionRepository:
    return AuroraDataAPIApprovalExecutionRepository(
        resource_arn=(
            "arn:aws:rds:us-east-1:"
            "123456789012:cluster:test"
        ),
        secret_arn=(
            "arn:aws:secretsmanager:us-east-1:"
            "123456789012:secret:test"
        ),
        database="cloudguard",
        region="us-east-1",
        client=client,
    )


def _statement_calls(
    client: RecordingRDSDataClient,
) -> list[dict[str, Any]]:
    return [
        kwargs
        for name, kwargs in client.calls
        if name == "execute_statement"
    ]


@pytest.mark.asyncio
async def test_success_is_one_atomic_tenant_transaction() -> None:
    client = RecordingRDSDataClient()
    repository = _repository(client)

    result = await repository.execute_for_workflow(
        organization_id=ORG_ID,
        workflow_execution_id=EXECUTION_ID,
    )

    assert (
        result.disposition
        is ActionExecutionDisposition.SUCCEEDED
    )

    names = [
        name
        for name, _ in client.calls
    ]

    assert names == [
        "begin_transaction",
        "execute_statement",
        "execute_statement",
        "execute_statement",
        "execute_statement",
        "commit_transaction",
    ]

    statements = _statement_calls(client)

    assert "set_config" in statements[0]["sql"]
    assert "FOR UPDATE" in statements[1]["sql"]
    assert (
        "INSERT INTO security_reviews"
        in statements[2]["sql"]
    )
    assert (
        "execution_succeeded"
        in statements[3]["sql"]
    )

    transaction_ids = {
        call["transactionId"]
        for call in statements
    }

    assert transaction_ids == {
        "transaction-1",
    }


@pytest.mark.asyncio
async def test_modified_executes_manager_replacement_only() -> None:
    replacement = _action(
        summary="Manager replacement.",
    )

    client = RecordingRDSDataClient(
        decision=ApprovalDecision.MODIFIED,
        modified_action=replacement,
    )

    result = await _repository(
        client
    ).execute_for_workflow(
        organization_id=ORG_ID,
        workflow_execution_id=EXECUTION_ID,
    )

    assert (
        result.disposition
        is ActionExecutionDisposition.SUCCEEDED
    )

    insert_call = next(
        call
        for call in _statement_calls(client)
        if "INSERT INTO security_reviews"
        in call["sql"]
    )

    parameters = {
        parameter["name"]: parameter["value"]
        for parameter in insert_call["parameters"]
    }

    assert (
        parameters["summary"]["stringValue"]
        == "Manager replacement."
    )


@pytest.mark.asyncio
async def test_invalid_action_records_terminal_failure_without_side_effect() -> None:
    client = RecordingRDSDataClient(
        proposed_action=_action(
            summary="Forbidden.",
            action_type="delete_resource",
        )
    )

    result = await _repository(
        client
    ).execute_for_workflow(
        organization_id=ORG_ID,
        workflow_execution_id=EXECUTION_ID,
    )

    assert (
        result.disposition
        is ActionExecutionDisposition.FAILED
    )
    assert result.error_code == "action_not_allowed"

    statements = _statement_calls(client)

    assert not any(
        "INSERT INTO security_reviews"
        in call["sql"]
        for call in statements
    )

    assert any(
        "execution_failed"
        in call["sql"]
        for call in statements
    )

    assert client.calls[-1][0] == "commit_transaction"


@pytest.mark.asyncio
async def test_success_replay_cannot_repeat_side_effect() -> None:
    client = RecordingRDSDataClient(
        status=ApprovalStatus.EXECUTION_SUCCEEDED,
        stored_idempotency_key="approval:stored:key",
        stored_reference="security-review:stored",
    )

    result = await _repository(
        client
    ).execute_for_workflow(
        organization_id=ORG_ID,
        workflow_execution_id=EXECUTION_ID,
    )

    assert (
        result.disposition
        is ActionExecutionDisposition.REPLAYED
    )
    assert (
        result.execution_reference
        == "security-review:stored"
    )

    statements = _statement_calls(client)

    assert not any(
        "INSERT INTO security_reviews"
        in call["sql"]
        for call in statements
    )

    assert not any(
        "UPDATE approvals"
        in call["sql"]
        for call in statements
    )

    assert client.calls[-1][0] == "commit_transaction"


@pytest.mark.asyncio
async def test_missing_callback_delivery_fails_before_side_effect() -> None:
    client = RecordingRDSDataClient(
        callback_delivered=False
    )

    with pytest.raises(
        CallbackDeliveryNotCommittedError,
        match="requires a delivered workflow callback",
    ):
        await AuroraDataAPIApprovalExecutionRepository(
            resource_arn="arn:aws:rds:us-east-1:123456789012:cluster:test",
            secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test",
            database="cloudguard",
            region="us-east-1",
            client=client,
            max_readiness_attempts=3,
            readiness_backoff_seconds=0.0,
        ).execute_for_workflow(
            organization_id=ORG_ID,
            workflow_execution_id=EXECUTION_ID,
        )

    statements = _statement_calls(client)

    assert not any(
        "INSERT INTO security_reviews"
        in call["sql"]
        for call in statements
    )
    assert client.calls[-1][0] == "rollback_transaction"


@pytest.mark.asyncio
async def test_callback_synchronization_race_recovers_after_bounded_poll() -> None:
    client = RecordingRDSDataClient(
        callback_delivered=False
    )
    slept: list[float] = []

    async def _on_sleep(delay: float) -> None:
        slept.append(delay)
        # Simulate callback recovery worker committing mark_callback_delivered
        # once the execution worker releases its FOR UPDATE lock via rollback.
        assert client.calls[-1][0] == "rollback_transaction"
        client.callback_delivered = True

    repo = AuroraDataAPIApprovalExecutionRepository(
        resource_arn="arn:aws:rds:us-east-1:123456789012:cluster:test",
        secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test",
        database="cloudguard",
        region="us-east-1",
        client=client,
        max_readiness_attempts=3,
        readiness_backoff_seconds=0.05,
        sleep=_on_sleep,
    )

    result = await repo.execute_for_workflow(
        organization_id=ORG_ID,
        workflow_execution_id=EXECUTION_ID,
    )

    assert result.disposition is ActionExecutionDisposition.SUCCEEDED
    assert slept == [0.05]

    insert_statements = [
        call
        for call in _statement_calls(client)
        if "INSERT INTO security_reviews" in call["sql"]
    ]
    assert len(insert_statements) == 1
    assert client.calls[-1][0] == "commit_transaction"


@pytest.mark.asyncio
async def test_provider_failure_rolls_back_and_is_sanitized() -> None:
    client = RecordingRDSDataClient(
        fail_sql_contains="INSERT INTO security_reviews"
    )

    with pytest.raises(
        UpstreamError
    ) as caught:
        await _repository(
            client
        ).execute_for_workflow(
            organization_id=ORG_ID,
            workflow_execution_id=EXECUTION_ID,
        )

    assert (
        "provider-secret-details"
        not in str(caught.value)
    )

    names = [
        name
        for name, _ in client.calls
    ]

    assert "rollback_transaction" in names
    assert "commit_transaction" not in names


@pytest.mark.asyncio
async def test_readiness_rollback_failure_aborts_immediately_without_second_poll() -> None:
    class RollbackFailingClient(RecordingRDSDataClient):
        def rollback_transaction(self, **kwargs: Any) -> dict[str, object]:
            self.calls.append(("rollback_transaction", kwargs))
            raise RuntimeError("secret-rds-rollback-error")

    client = RollbackFailingClient(callback_delivered=False)
    slept: list[float] = []

    async def _on_sleep(delay: float) -> None:
        slept.append(delay)

    repo = AuroraDataAPIApprovalExecutionRepository(
        resource_arn="arn:aws:rds:us-east-1:123456789012:cluster:test",
        secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test",
        database="cloudguard",
        region="us-east-1",
        client=client,
        max_readiness_attempts=4,
        readiness_backoff_seconds=0.05,
        sleep=_on_sleep,
    )

    with pytest.raises(UpstreamError) as caught:
        await repo.execute_for_workflow(
            organization_id=ORG_ID,
            workflow_execution_id=EXECUTION_ID,
        )

    assert "secret-rds-rollback-error" not in str(caught.value)
    assert slept == []

    names = [name for name, _ in client.calls]
    assert names == [
        "begin_transaction",
        "execute_statement",
        "execute_statement",
        "rollback_transaction",
    ]
    assert not any(
        "INSERT INTO security_reviews" in call["sql"]
        for call in _statement_calls(client)
    )
