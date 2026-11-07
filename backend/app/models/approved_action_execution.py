"""Trusted Phase 7.5 approved-action execution contracts."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.approval import ApprovalAction
from app.models.enums import ApprovalDecision


class ActionExecutionDisposition(StrEnum):
    """Result of attempting one approved-action execution."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    REPLAYED = "replayed"


class ApprovedActionProjection(BaseModel):
    """Server-derived action authorized by one immutable human decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: UUID
    organization_id: UUID
    workflow_execution_id: str = Field(min_length=1, max_length=256)
    decision: ApprovalDecision
    action: ApprovalAction | None
    idempotency_key: str = Field(min_length=1, max_length=256)


class ActionExecutionResult(BaseModel):
    """Bounded execution result that never contains a task token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=256)
    disposition: ActionExecutionDisposition
    action_type: str | None = Field(default=None, max_length=128)
    resource_id: UUID | None = None
    execution_reference: str | None = Field(default=None, max_length=256)
    error_code: str | None = Field(default=None, max_length=128)
