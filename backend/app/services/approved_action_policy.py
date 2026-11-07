"""Pure allowlist validation for the bounded Phase 7 demo action."""

from __future__ import annotations

from uuid import UUID

from app.models.approval import ApprovalAction
from app.models.approved_action_execution import ApprovedActionProjection


class BoundedActionValidationError(ValueError):
    """Persisted human action is outside the Phase 7 execution boundary."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def validate_phase7_demo_action(
    projection: ApprovedActionProjection,
) -> ApprovalAction:
    """Validate the one consequential action Phase 7 is allowed to execute."""
    action = projection.action

    if action is None:
        raise BoundedActionValidationError(
            "The bounded executor cannot execute an empty action.",
            error_code="empty_action",
        )

    if (
        action.action_type != "create_security_review"
        or action.resource_type != "workflow_execution"
    ):
        raise BoundedActionValidationError(
            "The approved action is outside the Phase 7 execution allowlist.",
            error_code="action_not_allowed",
        )

    try:
        workflow_execution_id = UUID(
            projection.workflow_execution_id
        )
    except ValueError as exc:
        raise BoundedActionValidationError(
            "The workflow execution identifier is invalid.",
            error_code="invalid_workflow_execution",
        ) from exc

    if action.resource_id != workflow_execution_id:
        raise BoundedActionValidationError(
            "The approved action resource does not match the workflow execution.",
            error_code="resource_mismatch",
        )

    return action
