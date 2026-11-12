"""Unit tests for the deployed Approval creation worker contract."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.deployed_approval_creation_worker import ApprovalCreationWorkflowTaskInput
from app.models.agent_workflow import (
    AgentWorkflowEvidence,
    AgentWorkflowResearchResult,
)
from app.models.ai import TokenUsage
from app.models.enums import Role
from app.models.principal import Principal
from app.models.reviewer_agent import ReviewDecision, ReviewerAgentResult
from pydantic import ValidationError

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
EXECUTION_ID = UUID("33333333-3333-4333-8333-333333333333")
CHUNK_ID = UUID("44444444-4444-4444-8444-444444444444")
DOCUMENT_ID = UUID("55555555-5555-4555-8555-555555555555")


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


def _reviewer() -> ReviewerAgentResult:
    return ReviewerAgentResult(
        decision=ReviewDecision.PASS,
        reasons=[],
        evidence_count=1,
        model_id="mock:reviewer-v1",
        usage=TokenUsage(input_tokens=8, output_tokens=3),
    )


def _payload() -> dict[str, object]:
    return {
        "execution_id": str(EXECUTION_ID),
        "correlation_id": "correlation-123",
        "question": "Should this workflow proceed?",
        "principal": _principal().model_dump(mode="json"),
        "research": _research().model_dump(mode="json"),
        "reviewer": _reviewer().model_dump(mode="json"),
        "task_token": "server-owned-step-functions-token",
    }


def test_worker_input_accepts_internal_task_token() -> None:
    parsed = ApprovalCreationWorkflowTaskInput.model_validate(_payload())

    assert parsed.task_token == "server-owned-step-functions-token"
    assert parsed.principal.organization_id == ORG_ID
    assert parsed.reviewer.decision is ReviewDecision.PASS


def test_worker_input_forbids_unknown_fields() -> None:
    payload = _payload()
    payload["organization_id"] = str(ORG_ID)

    with pytest.raises(ValidationError):
        ApprovalCreationWorkflowTaskInput.model_validate(payload)


def test_worker_input_requires_task_token() -> None:
    payload = _payload()
    del payload["task_token"]

    with pytest.raises(ValidationError):
        ApprovalCreationWorkflowTaskInput.model_validate(payload)


@pytest.mark.parametrize("env_name", ["dev", "staging", "prod"])
def test_approval_creation_worker_starts_with_synthesized_env_without_cognito_or_guardrail(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
) -> None:
    """Approval creation worker starts in dev/staging/prod without Cognito or Guardrail."""
    import app.deployed_approval_creation_worker as worker
    from app.adapters.aws.approval_creation_repository import (
        AuroraDataAPIApprovalCreationRepository,
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
        assert isinstance(repo, AuroraDataAPIApprovalCreationRepository)
    finally:
        get_approval_worker_settings.cache_clear()
