"""Trusted deployed worker for Phase 7.5 approved-action execution."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.adapters.aws.approval_execution_repository import (
    AuroraDataAPIApprovalExecutionRepository,
)
from app.core.config import get_settings
from app.models.approved_action_execution import (
    ActionExecutionDisposition,
)
from app.models.principal import Principal


class ApprovedActionExecutionWorkflowTaskInput(BaseModel):
    """Minimal trusted workflow input; action data is never accepted."""

    model_config = ConfigDict(extra="forbid")

    execution_id: UUID
    principal: Principal


def _build_repository() -> AuroraDataAPIApprovalExecutionRepository:
    settings = get_settings()

    resource_arn = settings.aws.aurora_cluster_arn
    secret_arn = settings.aws.aurora_secret_arn

    if resource_arn is None or secret_arn is None:
        raise RuntimeError(
            "Approved-action execution requires Aurora "
            "cluster and secret ARNs."
        )

    return AuroraDataAPIApprovalExecutionRepository(
        resource_arn=resource_arn,
        secret_arn=secret_arn,
        database=settings.database.name,
        region=settings.aws.region,
        endpoint_url=settings.aws.endpoint_url,
    )


async def _handle(
    event: dict[str, Any],
) -> dict[str, Any]:
    task_input = (
        ApprovedActionExecutionWorkflowTaskInput.model_validate(
            event
        )
    )

    settings = get_settings()
    if not settings.features.agentic_workflows:
        raise RuntimeError(
            "Agentic workflows are disabled."
        )

    result = await _build_repository().execute_for_workflow(
        organization_id=task_input.principal.organization_id,
        workflow_execution_id=task_input.execution_id,
    )

    if result.disposition is ActionExecutionDisposition.FAILED:
        raise RuntimeError(
            "Approved action execution failed."
        )

    return result.model_dump(
        mode="json"
    )


def handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    """Lambda entrypoint for the post-approval execution state."""
    del context
    return asyncio.run(
        _handle(event)
    )
