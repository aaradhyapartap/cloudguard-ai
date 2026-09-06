"""Port for the durable Phase 7 approved-action execution transaction."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.models.approved_action_execution import ActionExecutionResult


class ApprovalExecutionRepository(Protocol):
    async def execute_for_workflow(
        self,
        *,
        organization_id: UUID,
        workflow_execution_id: UUID,
    ) -> ActionExecutionResult:
        ...
