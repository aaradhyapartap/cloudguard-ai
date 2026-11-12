"""Tests for the deployed approved-action execution worker boundary."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.deployed_approved_action_execution_worker import (
    ApprovedActionExecutionWorkflowTaskInput,
)
from pydantic import ValidationError

EXECUTION_ID = UUID("22222222-2222-4222-8222-222222222222")
ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")


def _event() -> dict[str, object]:
    return {
        "execution_id": str(EXECUTION_ID),
        "principal": {
            "user_id": str(USER_ID),
            "organization_id": str(ORG_ID),
            "role": "manager",
            "email": "manager@example.test",
            "department": "Security",
        },
    }


def test_worker_accepts_only_execution_identity_and_principal() -> None:
    parsed = ApprovedActionExecutionWorkflowTaskInput.model_validate(
        _event()
    )

    assert parsed.execution_id == EXECUTION_ID
    assert parsed.principal.organization_id == ORG_ID


def test_worker_rejects_supplied_action() -> None:
    event = _event()
    event["action"] = {
        "action_type": "delete_resource",
        "resource_type": "workflow_execution",
        "resource_id": str(EXECUTION_ID),
        "summary": "forged",
    }

    with pytest.raises(ValidationError):
        ApprovedActionExecutionWorkflowTaskInput.model_validate(
            event
        )


def test_worker_rejects_supplied_organization_override() -> None:
    event = _event()
    event["organization_id"] = str(
        UUID("99999999-9999-4999-8999-999999999999")
    )

    with pytest.raises(ValidationError):
        ApprovedActionExecutionWorkflowTaskInput.model_validate(
            event
        )


@pytest.mark.parametrize("env_name", ["dev", "staging", "prod"])
def test_approved_action_execution_worker_starts_with_synthesized_env_without_cognito_or_guardrail(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
) -> None:
    """Approved-action worker starts in dev/staging/prod without Cognito or Guardrail."""
    import app.deployed_approved_action_execution_worker as worker
    from app.adapters.aws.approval_execution_repository import (
        AuroraDataAPIApprovalExecutionRepository,
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
        assert isinstance(repo, AuroraDataAPIApprovalExecutionRepository)
    finally:
        get_approval_worker_settings.cache_clear()
