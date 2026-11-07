"""Port for executing one server-projected approved action."""

from __future__ import annotations

from typing import Protocol

from app.models.approved_action_execution import (
    ActionExecutionResult,
    ApprovedActionProjection,
)


class ApprovedActionExecutor(Protocol):
    """Execute only an already-authorized bounded action."""

    async def execute(
        self,
        *,
        projection: ApprovedActionProjection,
    ) -> ActionExecutionResult:
        ...
