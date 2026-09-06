"""Local Approval callback adapter used by deterministic tests."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.approval import ApprovalAction
from app.models.enums import ApprovalDecision


@dataclass(frozen=True)
class RecordedApprovalCallback:
    """One internal workflow-resume call captured by the local adapter."""

    task_token: str
    decision: ApprovalDecision
    effective_action: ApprovalAction | None


class LocalApprovalCallback:
    """In-memory callback test double; never calls AWS."""

    def __init__(self) -> None:
        self.calls: list[RecordedApprovalCallback] = []

    async def send_decision(
        self,
        *,
        task_token: str,
        decision: ApprovalDecision,
        effective_action: ApprovalAction | None,
    ) -> None:
        self.calls.append(
            RecordedApprovalCallback(
                task_token=task_token,
                decision=decision,
                effective_action=effective_action,
            )
        )
