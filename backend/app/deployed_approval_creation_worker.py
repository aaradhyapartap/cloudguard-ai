"""Deployed Step Functions task that creates a pending human Approval."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_agent_worker_settings
from app.core.logging import configure_logging
from app.models.agent_workflow import AgentWorkflowResearchResult
from app.models.principal import Principal
from app.models.reviewer_agent import ReviewerAgentResult
from app.security.worker_errors import sanitize_worker_failures
from app.services.approval_creation import ApprovalCreationService


class ApprovalCreationWorkflowTaskInput(BaseModel):
    """Trusted workflow state entering the human-approval wait task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    principal: Principal
    research: AgentWorkflowResearchResult
    reviewer: ReviewerAgentResult
    task_token: str = Field(min_length=1)


_settings = get_agent_worker_settings()
configure_logging(_settings)


def _build_repository() -> Any:
    """Build the deployed Aurora Data API Approval creation repository."""
    from app.adapters.aws.approval_creation_repository import (
        AuroraDataAPIApprovalCreationRepository,
    )

    resource_arn = _settings.aws.aurora_cluster_arn
    secret_arn = _settings.aws.aurora_secret_arn

    if not resource_arn:
        raise RuntimeError(
            "AWS_AURORA_CLUSTER_ARN is required for deployed Approval creation."
        )
    if not secret_arn:
        raise RuntimeError(
            "AWS_AURORA_SECRET_ARN is required for deployed Approval creation."
        )

    return AuroraDataAPIApprovalCreationRepository(
        resource_arn=resource_arn,
        secret_arn=secret_arn,
        database=_settings.database.name,
        region=_settings.aws.region,
        endpoint_url=_settings.aws.endpoint_url,
    )


async def _handle(event: dict[str, Any]) -> dict[str, Any]:
    task_input = ApprovalCreationWorkflowTaskInput.model_validate(event)

    if not _settings.features.agentic_workflows:
        raise RuntimeError("Agentic workflows are disabled.")

    service = ApprovalCreationService(repository=_build_repository())

    pending = await service.create_from_workflow(
        execution_id=task_input.execution_id,
        principal=task_input.principal,
        research=task_input.research,
        reviewer=task_input.reviewer,
        task_token=task_input.task_token,
    )

    return {
        "execution_id": task_input.execution_id,
        "correlation_id": task_input.correlation_id,
        "question": task_input.question,
        "principal": task_input.principal.model_dump(mode="json"),
        "research": task_input.research.model_dump(mode="json"),
        "reviewer": task_input.reviewer.model_dump(mode="json"),
        "approval": pending.model_dump(mode="json"),
    }


@sanitize_worker_failures
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entrypoint for the Approval creation wait state."""
    del context
    return asyncio.run(_handle(event))
