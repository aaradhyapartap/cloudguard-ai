"""Callback synchronization race regressions (CG-14).

Exercises both sides of the Step Functions callback boundary:
1. ``ApprovalCallbackService`` never marks delivery complete before provider
   success and fails closed if ``mark_callback_delivered`` does not commit.
2. ``AuroraDataAPIApprovalExecutionRepository`` handles the race where
   ``SendTaskSuccess`` resumes Step Functions before ``callback_delivered_at``
   commits: it rolls back its ``FOR UPDATE`` lock so the callback worker can
   commit during bounded readiness polls, executes the approved side effect at
   most once once ready, and fails closed with ``CallbackDeliveryNotCommittedError``
   if delivery state remains uncommitted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.adapters.aws.approval_execution_repository import (
    AuroraDataAPIApprovalExecutionRepository,
    CallbackDeliveryNotCommittedError,
)
from app.models.approved_action_execution import ActionExecutionDisposition
from app.models.enums import ApprovalDecision, ApprovalStatus
from app.services.approval_callback import ApprovalCallbackService

pytestmark = pytest.mark.security

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
EXECUTION_ID = UUID("22222222-2222-4222-8222-222222222222")
APPROVAL_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RECOMMENDATION_ID = UUID("33333333-3333-4333-8333-333333333333")
APPROVER_ID = UUID("44444444-4444-4444-8444-444444444444")


def _field(value: str | None) -> dict[str, object]:
    if value is None:
        return {"isNull": True}
    return {"stringValue": value}


class FakeCallbackClaim:
    """Simulates a claimed callback record."""

    def __init__(
        self,
        *,
        task_token: str,
        decision: ApprovalDecision,
        effective_action: str | None,
    ) -> None:
        self.claim_id = uuid4()
        self.task_token = task_token
        self.decision = decision
        self.effective_action = effective_action


class FakeRecoveryRepository:
    """In-memory callback recovery repository for race regressions."""

    def __init__(
        self,
        *,
        claim: FakeCallbackClaim | None = None,
        mark_delivered_returns: bool = True,
    ) -> None:
        self._claim = claim
        self._mark_delivered_returns = mark_delivered_returns
        self.release_called = False

    async def claim_decided_callback(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        stale_before: datetime,
    ) -> FakeCallbackClaim | None:
        return self._claim

    async def release_callback_claim(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        claim_id: UUID,
    ) -> None:
        self.release_called = True

    async def mark_callback_delivered(
        self,
        *,
        organization_id: UUID,
        approval_id: UUID,
        claim_id: UUID,
    ) -> bool:
        return self._mark_delivered_returns


class FakeCallbackPort:
    """Records whether send_decision was invoked and optionally fails."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.sent = False
        self._should_fail = should_fail

    async def send_decision(
        self,
        *,
        task_token: str,
        decision: ApprovalDecision,
        effective_action: str | None,
    ) -> None:
        if self._should_fail:
            raise RuntimeError("Step Functions SendTaskSuccess failed")
        self.sent = True


class RacingRDSDataClient:
    """Simulates RDS Data API where callback_delivered_at commits after N polls."""

    def __init__(self, *, deliver_on_attempt: int | None) -> None:
        self.deliver_on_attempt = deliver_on_attempt
        self.select_attempts = 0
        self.insert_count = 0
        self.calls: list[str] = []

    def begin_transaction(self, **kwargs: Any) -> dict[str, str]:
        del kwargs
        self.calls.append("begin_transaction")
        return {"transactionId": f"tx-{len(self.calls)}"}

    def commit_transaction(self, **kwargs: Any) -> dict[str, object]:
        del kwargs
        self.calls.append("commit_transaction")
        return {}

    def rollback_transaction(self, **kwargs: Any) -> dict[str, object]:
        del kwargs
        self.calls.append("rollback_transaction")
        return {}

    def execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        sql = str(kwargs["sql"])
        self.calls.append(sql)

        if sql.startswith("SELECT set_config"):
            return {}

        if "FROM approvals" in sql and "FOR UPDATE" in sql:
            self.select_attempts += 1
            delivered = (
                self.deliver_on_attempt is not None
                and self.select_attempts >= self.deliver_on_attempt
            )
            action = {
                "action_type": "create_security_review",
                "resource_type": "workflow_execution",
                "resource_id": str(EXECUTION_ID),
                "summary": "Human-approved security review.",
            }
            return {
                "records": [
                    [
                        _field(str(APPROVAL_ID)),
                        _field(str(ORG_ID)),
                        _field(str(EXECUTION_ID)),
                        _field(str(RECOMMENDATION_ID)),
                        _field(json.dumps(action, separators=(",", ":"), sort_keys=True)),
                        _field("[]"),
                        _field(None),
                        _field("[]"),
                        _field(None),
                        _field("mock:reviewer"),
                        _field(ApprovalStatus.DECIDED.value),
                        _field("2026-09-06T08:00:00+00:00"),
                        _field(ApprovalDecision.APPROVED.value),
                        _field(str(APPROVER_ID)),
                        _field("2026-09-06T08:05:00+00:00"),
                        _field(None),
                        _field(None),
                        _field(None),
                        _field("2026-09-06T08:06:00+00:00" if delivered else None),
                        _field(None),
                        _field(None),
                        _field(None),
                        _field(None),
                    ]
                ]
            }

        if "INSERT INTO security_reviews" in sql:
            self.insert_count += 1
            return {"records": [[{"stringValue": "55555555-5555-4555-8555-555555555555"}]]}

        if "UPDATE approvals" in sql and "execution_succeeded" in sql:
            return {"records": [[{"stringValue": str(APPROVAL_ID)}]]}

        return {}


class TestCallbackRace:
    """Reproduces the callback delivery / database commit race condition."""

    @pytest.mark.asyncio
    async def test_delivery_success_but_marker_commit_fails_raises(self) -> None:
        claim = FakeCallbackClaim(
            task_token="arn:aws:states:task-token-123",
            decision=ApprovalDecision.APPROVED,
            effective_action="create_security_review",
        )
        repo = FakeRecoveryRepository(claim=claim, mark_delivered_returns=False)
        callback = FakeCallbackPort()

        service = ApprovalCallbackService(
            repository=repo,  # type: ignore[arg-type]
            callback=callback,  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="delivery marker"):
            await service.resume_decided(
                organization_id=uuid4(),
                approval_id=uuid4(),
                stale_before=datetime.now(UTC),
            )

        assert callback.sent is True

    @pytest.mark.asyncio
    async def test_callback_failure_releases_claim_without_marking_delivered(self) -> None:
        claim = FakeCallbackClaim(
            task_token="arn:aws:states:task-token-456",
            decision=ApprovalDecision.REJECTED,
            effective_action=None,
        )
        repo = FakeRecoveryRepository(claim=claim)
        callback = FakeCallbackPort(should_fail=True)

        service = ApprovalCallbackService(
            repository=repo,  # type: ignore[arg-type]
            callback=callback,  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="Step Functions"):
            await service.resume_decided(
                organization_id=uuid4(),
                approval_id=uuid4(),
                stale_before=datetime.now(UTC),
            )

        assert repo.release_called is True

    @pytest.mark.asyncio
    async def test_never_marks_delivered_before_provider_success(self) -> None:
        call_order: list[str] = []

        class OrderTrackingRepo(FakeRecoveryRepository):
            async def mark_callback_delivered(self, **kwargs: object) -> bool:
                del kwargs
                call_order.append("mark_delivered")
                return True

        class OrderTrackingCallback(FakeCallbackPort):
            async def send_decision(self, **kwargs: object) -> None:
                del kwargs
                call_order.append("send_decision")

        claim = FakeCallbackClaim(
            task_token="arn:aws:states:task-token-789",
            decision=ApprovalDecision.APPROVED,
            effective_action="create_security_review",
        )
        service = ApprovalCallbackService(
            repository=OrderTrackingRepo(claim=claim),  # type: ignore[arg-type]
            callback=OrderTrackingCallback(),
        )

        await service.resume_decided(
            organization_id=uuid4(),
            approval_id=uuid4(),
            stale_before=datetime.now(UTC),
        )

        assert call_order == ["send_decision", "mark_delivered"]

    @pytest.mark.asyncio
    async def test_execution_repository_reconciles_inflight_callback_commit_race(self) -> None:
        client = RacingRDSDataClient(deliver_on_attempt=3)
        slept: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            slept.append(seconds)
            assert client.calls[-1] == "rollback_transaction"

        repository = AuroraDataAPIApprovalExecutionRepository(
            resource_arn="arn:aws:rds:us-east-1:123456789012:cluster:test",
            secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test",
            database="cloudguard",
            region="us-east-1",
            client=client,
            max_readiness_attempts=4,
            readiness_backoff_seconds=0.05,
            sleep=_fake_sleep,
        )

        result = await repository.execute_for_workflow(
            organization_id=ORG_ID,
            workflow_execution_id=EXECUTION_ID,
        )

        assert result.disposition is ActionExecutionDisposition.SUCCEEDED
        assert client.select_attempts == 3
        assert len(slept) == 2
        assert client.insert_count == 1
        assert client.calls[-1] == "commit_transaction"

    @pytest.mark.asyncio
    async def test_execution_repository_fails_closed_when_callback_never_commits(self) -> None:
        client = RacingRDSDataClient(deliver_on_attempt=None)
        slept: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        repository = AuroraDataAPIApprovalExecutionRepository(
            resource_arn="arn:aws:rds:us-east-1:123456789012:cluster:test",
            secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test",
            database="cloudguard",
            region="us-east-1",
            client=client,
            max_readiness_attempts=3,
            readiness_backoff_seconds=0.05,
            sleep=_fake_sleep,
        )

        with pytest.raises(
            CallbackDeliveryNotCommittedError,
            match="requires a delivered workflow callback",
        ):
            await repository.execute_for_workflow(
                organization_id=ORG_ID,
                workflow_execution_id=EXECUTION_ID,
            )

        assert client.select_attempts == 3
        assert len(slept) == 2
        assert client.insert_count == 0
        assert client.calls[-1] == "rollback_transaction"

    @pytest.mark.asyncio
    async def test_execution_repository_fails_closed_when_readiness_rollback_fails(
        self,
    ) -> None:
        class RollbackFailingRaceClient(RacingRDSDataClient):
            def rollback_transaction(self, **kwargs: Any) -> dict[str, object]:
                del kwargs
                self.calls.append("rollback_transaction")
                raise RuntimeError("sensitive-rds-rollback-failure")

        client = RollbackFailingRaceClient(deliver_on_attempt=2)
        slept: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        repository = AuroraDataAPIApprovalExecutionRepository(
            resource_arn="arn:aws:rds:us-east-1:123456789012:cluster:test",
            secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test",
            database="cloudguard",
            region="us-east-1",
            client=client,
            max_readiness_attempts=4,
            readiness_backoff_seconds=0.05,
            sleep=_fake_sleep,
        )

        from app.core.errors import UpstreamError

        with pytest.raises(UpstreamError) as exc_info:
            await repository.execute_for_workflow(
                organization_id=ORG_ID,
                workflow_execution_id=EXECUTION_ID,
            )

        assert "sensitive-rds-rollback-failure" not in str(exc_info.value)
        assert client.select_attempts == 1
        assert slept == []
        assert client.insert_count == 0
        assert client.calls.count("begin_transaction") == 1
