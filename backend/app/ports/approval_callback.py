"""Port for resuming a trusted Step Functions approval wait."""

from __future__ import annotations

from typing import Protocol

from app.models.approval import ApprovalAction
from app.models.enums import ApprovalDecision


class ApprovalCallbackPort(Protocol):
    """Resume a paused workflow using a server-resolved task token."""

    async def send_decision(
        self,
        *,
        task_token: str,
        decision: ApprovalDecision,
        effective_action: ApprovalAction | None,
    ) -> None:
        """Resume the waiting workflow with validated human decision data."""
        ...
