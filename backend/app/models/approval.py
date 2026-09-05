"""Provider-neutral contracts for Phase 7 human approval."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.enums import ApprovalDecision, ApprovalStatus

BoundedText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]

ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]


class ApprovalAction(BaseModel):
    """Bounded consequential action presented to or modified by a Manager."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_type: ShortText
    resource_type: ShortText
    resource_id: UUID
    summary: BoundedText


class ApprovalEvidenceReference(BaseModel):
    """Trusted evidence identity carried into an approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: UUID
    document_id: UUID


def _validate_unique_evidence(
    evidence: list[ApprovalEvidenceReference],
) -> None:
    chunk_ids = [reference.chunk_id for reference in evidence]

    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("Approval evidence chunk_id values must be unique.")


class ApprovalScoreContext(BaseModel):
    """Deterministic score context shown to the approving Manager."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    scoring_version: ShortText


class PendingApproval(BaseModel):
    """Public representation of an approval awaiting a human decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    organization_id: UUID
    workflow_execution_id: ShortText
    recommendation_id: UUID
    proposed_action: ApprovalAction
    evidence: list[ApprovalEvidenceReference] = Field(default_factory=list, max_length=10)
    score_context: ApprovalScoreContext | None = None
    agent_trace_ids: list[ShortText] = Field(default_factory=list, max_length=16)
    generator_model_id: ShortText | None = None
    reviewer_model_id: ShortText | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime

    @model_validator(mode="after")
    def validate_pending_state(self) -> PendingApproval:
        if self.status is not ApprovalStatus.PENDING:
            raise ValueError("PendingApproval status must be pending.")

        _validate_unique_evidence(self.evidence)

        return self


class ApprovalDecisionRequest(BaseModel):
    """Manager-supplied decision body.

    Identity, tenant, permissions, and task token are intentionally absent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ApprovalDecision
    justification: BoundedText | None = None
    comment: BoundedText | None = None
    modified_action: ApprovalAction | None = None

    @model_validator(mode="after")
    def validate_decision_payload(self) -> ApprovalDecisionRequest:
        if self.decision is ApprovalDecision.APPROVED:
            if self.modified_action is not None:
                raise ValueError("Approved decision cannot include a modified action.")

        elif self.decision is ApprovalDecision.REJECTED:
            if self.justification is None:
                raise ValueError("Rejected decision requires justification.")
            if self.modified_action is not None:
                raise ValueError("Rejected decision cannot include a modified action.")

        elif self.decision is ApprovalDecision.MODIFIED:
            if self.justification is None:
                raise ValueError("Modified decision requires justification.")
            if self.modified_action is None:
                raise ValueError("Modified decision requires a replacement action.")

        return self


class DecidedApproval(BaseModel):
    """Public representation of an immutable recorded Manager decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    organization_id: UUID
    workflow_execution_id: ShortText
    recommendation_id: UUID
    proposed_action: ApprovalAction
    evidence: list[ApprovalEvidenceReference] = Field(default_factory=list, max_length=10)
    score_context: ApprovalScoreContext | None = None
    agent_trace_ids: list[ShortText] = Field(default_factory=list, max_length=16)
    generator_model_id: ShortText | None = None
    reviewer_model_id: ShortText | None = None
    status: ApprovalStatus = ApprovalStatus.DECIDED
    decision: ApprovalDecision
    approver_id: UUID
    decided_at: datetime
    justification: BoundedText | None = None
    comment: BoundedText | None = None
    modified_action: ApprovalAction | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_decided_state(self) -> DecidedApproval:
        if self.status is not ApprovalStatus.DECIDED:
            raise ValueError("DecidedApproval status must be decided.")

        _validate_unique_evidence(self.evidence)

        ApprovalDecisionRequest(
            decision=self.decision,
            justification=self.justification,
            comment=self.comment,
            modified_action=self.modified_action,
        )

        return self


_ALLOWED_APPROVAL_TRANSITIONS: frozenset[
    tuple[ApprovalStatus, ApprovalStatus]
] = frozenset(
    {
        (ApprovalStatus.PENDING, ApprovalStatus.DECIDED),
        (ApprovalStatus.DECIDED, ApprovalStatus.EXECUTION_SUCCEEDED),
        (ApprovalStatus.DECIDED, ApprovalStatus.EXECUTION_FAILED),
    }
)


def validate_approval_transition(
    current: ApprovalStatus,
    target: ApprovalStatus,
) -> None:
    """Fail closed when an Approval lifecycle transition is not permitted."""

    if (current, target) not in _ALLOWED_APPROVAL_TRANSITIONS:
        raise ValueError(
            f"Invalid approval state transition: {current.value} -> {target.value}."
        )
