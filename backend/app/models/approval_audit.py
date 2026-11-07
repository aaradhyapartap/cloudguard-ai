"""Public/domain contract for immutable Phase 7 Approval audit events."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApprovalAuditEventType(StrEnum):
    APPROVAL_CREATED = "approval_created"
    APPROVAL_DECIDED = "approval_decided"
    CALLBACK_DELIVERED = "callback_delivered"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    EXECUTION_FAILED = "execution_failed"


class ApprovalAuditEvent(BaseModel):
    """Immutable audit projection that can never expose workflow secrets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    organization_id: UUID
    approval_id: UUID
    event_type: ApprovalAuditEventType
    actor_id: UUID | None = None
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
