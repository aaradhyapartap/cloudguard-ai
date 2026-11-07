"""Trusted recommendation contract bridging Reviewer PASS to human Approval."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.approval import (
    ApprovalAction,
    ApprovalEvidenceReference,
    ApprovalScoreContext,
)


class WorkflowRecommendation(BaseModel):
    """Exact bounded recommendation snapshot eligible for Manager approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recommendation_id: UUID
    proposed_action: ApprovalAction
    evidence: tuple[ApprovalEvidenceReference, ...] = Field(
        default_factory=tuple,
        max_length=10,
    )
    score_context: ApprovalScoreContext | None = None
    generator_model_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_unique_evidence(self) -> WorkflowRecommendation:
        chunk_ids = [reference.chunk_id for reference in self.evidence]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("Workflow recommendation evidence must be unique.")
        return self
