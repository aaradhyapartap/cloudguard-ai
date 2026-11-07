"""Phase 7.5 human-approved action execution boundary."""

from __future__ import annotations

from app.models.approval import DecidedApproval
from app.models.approved_action_execution import (
    ActionExecutionDisposition,
    ActionExecutionResult,
)
from app.ports.approved_action_executor import ApprovedActionExecutor
from app.services.approved_action_projection import ApprovedActionProjector


class ApprovedActionExecutionService:
    """Execute only the exact action authorized by persisted human state."""

    def __init__(
        self,
        *,
        executor: ApprovedActionExecutor,
    ) -> None:
        self._executor = executor

    async def execute(
        self,
        *,
        approval: DecidedApproval,
    ) -> ActionExecutionResult:
        projection = ApprovedActionProjector.project(
            approval
        )

        if projection.action is None:
            return ActionExecutionResult(
                approval_id=projection.approval_id,
                idempotency_key=projection.idempotency_key,
                disposition=ActionExecutionDisposition.SKIPPED,
            )

        return await self._executor.execute(
            projection=projection
        )
