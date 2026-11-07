"""Unit tests for trusted workflow Approval creation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.models.agent_workflow import (
    AgentWorkflowEvidence,
    AgentWorkflowResearchResult,
)
from app.models.ai import TokenUsage
from app.models.approval import PendingApproval
from app.models.enums import ApprovalStatus, Role
from app.models.principal import Principal
from app.models.reviewer_agent import (
    ReviewDecision,
    ReviewerAgentResult,
    ReviewReason,
)
from app.services.approval_creation import ApprovalCreationService

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
EXECUTION_ID = UUID("33333333-3333-4333-8333-333333333333")
CHUNK_ID = UUID("44444444-4444-4444-8444-444444444444")
DOCUMENT_ID = UUID("55555555-5555-4555-8555-555555555555")
APPROVAL_ID = UUID("66666666-6666-4666-8666-666666666666")


def _principal() -> Principal:
    return Principal(
        user_id=USER_ID,
        organization_id=ORG_ID,
        email="analyst@example.com",
        role=Role.ANALYST,
    )


def _research() -> AgentWorkflowResearchResult:
    return AgentWorkflowResearchResult(
        evidence=[
            AgentWorkflowEvidence(
                chunk_id=str(CHUNK_ID),
                document_id=str(DOCUMENT_ID),
                content="Trusted evidence.",
                score=0.95,
            )
        ],
        retrieval_count=1,
        tool_calls_used=1,
        model_id="mock:research-v1",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def _reviewer(
    decision: ReviewDecision = ReviewDecision.PASS,
) -> ReviewerAgentResult:
    reasons = (
        []
        if decision is ReviewDecision.PASS
        else [ReviewReason(message="Reviewer rejected the workflow.")]
    )
    return ReviewerAgentResult(
        decision=decision,
        reasons=reasons,
        evidence_count=1,
        model_id="mock:reviewer-v1",
        usage=TokenUsage(input_tokens=8, output_tokens=3),
    )


class RecordingApprovalRepository:
    def __init__(self) -> None:
        self.create_kwargs: dict[str, Any] | None = None

    async def create_pending(self, **kwargs: Any) -> PendingApproval:
        self.create_kwargs = kwargs
        return PendingApproval(
            id=APPROVAL_ID,
            organization_id=kwargs["organization_id"],
            workflow_execution_id=kwargs["workflow_execution_id"],
            recommendation_id=kwargs["recommendation_id"],
            proposed_action=kwargs["proposed_action"],
            evidence=kwargs["evidence"],
            score_context=kwargs["score_context"],
            agent_trace_ids=kwargs["agent_trace_ids"],
            generator_model_id=kwargs["generator_model_id"],
            reviewer_model_id=kwargs["reviewer_model_id"],
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(UTC),
        )

    async def get_by_id(self, **kwargs: Any) -> None:
        raise NotImplementedError

    async def list_pending(self, **kwargs: Any) -> list[PendingApproval]:
        raise NotImplementedError

    async def decide_pending(self, **kwargs: Any) -> None:
        raise NotImplementedError

    async def get_decided_callback_context(self, **kwargs: Any) -> None:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_creation_uses_principal_tenant_and_internal_task_token() -> None:
    repository = RecordingApprovalRepository()
    service = ApprovalCreationService(repository=repository)

    pending = await service.create_from_workflow(
        execution_id=str(EXECUTION_ID),
        principal=_principal(),
        research=_research(),
        reviewer=_reviewer(),
        task_token="server-owned-step-functions-token",
    )

    assert pending.organization_id == ORG_ID
    assert pending.workflow_execution_id == str(EXECUTION_ID)
    assert pending.reviewer_model_id == "mock:reviewer-v1"
    assert pending.generator_model_id is None
    assert pending.agent_trace_ids == ()
    assert pending.score_context is None

    assert repository.create_kwargs is not None
    assert repository.create_kwargs["organization_id"] == ORG_ID
    assert (
        repository.create_kwargs["task_token"]
        == "server-owned-step-functions-token"
    )

    assert not hasattr(pending, "task_token")
    assert "task_token" not in pending.model_dump()


@pytest.mark.asyncio
async def test_creation_rejects_reviewer_fail_before_persistence() -> None:
    repository = RecordingApprovalRepository()
    service = ApprovalCreationService(repository=repository)

    with pytest.raises(
        ValueError,
        match="Reviewer FAIL must not create",
    ):
        await service.create_from_workflow(
            execution_id=str(EXECUTION_ID),
            principal=_principal(),
            research=_research(),
            reviewer=_reviewer(ReviewDecision.FAIL),
            task_token="server-owned-step-functions-token",
        )

    assert repository.create_kwargs is None


@pytest.mark.asyncio
async def test_creation_rejects_non_uuid_execution_id() -> None:
    repository = RecordingApprovalRepository()
    service = ApprovalCreationService(repository=repository)

    with pytest.raises(
        ValueError,
        match="execution_id must be a valid UUID",
    ):
        await service.create_from_workflow(
            execution_id="not-a-uuid",
            principal=_principal(),
            research=_research(),
            reviewer=_reviewer(),
            task_token="server-owned-step-functions-token",
        )

    assert repository.create_kwargs is None

