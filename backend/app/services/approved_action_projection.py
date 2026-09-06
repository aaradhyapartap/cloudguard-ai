"""Project immutable Approval decisions into the only action allowed to execute."""

from __future__ import annotations

import hashlib
import json

from app.models.approval import DecidedApproval
from app.models.approved_action_execution import ApprovedActionProjection
from app.models.enums import ApprovalDecision, ApprovalStatus


class ApprovedActionProjector:
    """Derive execution authority from trusted persisted Approval state only."""

    @staticmethod
    def project(approval: DecidedApproval) -> ApprovedActionProjection:
        """Return the exact action authorized by the human decision.

        Rejected decisions deliberately project to no action.
        Terminal approvals cannot be projected again.
        """
        if approval.status is not ApprovalStatus.DECIDED:
            raise ValueError(
                "Only a decided Approval may enter the execution boundary."
            )

        if approval.decision is ApprovalDecision.APPROVED:
            action = approval.proposed_action

        elif approval.decision is ApprovalDecision.MODIFIED:
            if approval.modified_action is None:
                raise ValueError(
                    "A modified Approval must contain its validated replacement action."
                )
            action = approval.modified_action

        elif approval.decision is ApprovalDecision.REJECTED:
            action = None

        else:
            raise ValueError("Unsupported Approval decision.")

        canonical_action = (
            None
            if action is None
            else action.model_dump(mode="json")
        )

        canonical = json.dumps(
            {
                "approval_id": str(approval.id),
                "organization_id": str(approval.organization_id),
                "decision": approval.decision.value,
                "action": canonical_action,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

        digest = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        return ApprovedActionProjection(
            approval_id=approval.id,
            organization_id=approval.organization_id,
            workflow_execution_id=approval.workflow_execution_id,
            decision=approval.decision,
            action=action,
            idempotency_key=f"approval:{approval.id}:{digest}",
        )
