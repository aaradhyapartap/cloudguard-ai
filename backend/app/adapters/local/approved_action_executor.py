"""Local bounded executor for the Phase 7 security-review demo action."""

from __future__ import annotations

from app.models.approved_action_execution import (
    ActionExecutionDisposition,
    ActionExecutionResult,
    ApprovedActionProjection,
)
from app.services.approved_action_policy import validate_phase7_demo_action


class LocalSecurityReviewExecutor:
    """Execute exactly one bounded demo action with local replay protection."""

    def __init__(self) -> None:
        self._executed: dict[str, ActionExecutionResult] = {}

    async def execute(
        self,
        *,
        projection: ApprovedActionProjection,
    ) -> ActionExecutionResult:
        existing = self._executed.get(
            projection.idempotency_key
        )

        if existing is not None:
            return existing.model_copy(
                update={
                    "disposition": ActionExecutionDisposition.REPLAYED,
                }
            )

        action = validate_phase7_demo_action(
            projection
        )

        result = ActionExecutionResult(
            approval_id=projection.approval_id,
            idempotency_key=projection.idempotency_key,
            disposition=ActionExecutionDisposition.SUCCEEDED,
            action_type=action.action_type,
            resource_id=action.resource_id,
            execution_reference=(
                f"security-review:{projection.approval_id}"
            ),
        )

        self._executed[
            projection.idempotency_key
        ] = result

        return result
