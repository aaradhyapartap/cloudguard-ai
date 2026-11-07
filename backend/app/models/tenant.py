"""Tenant execution context for internal, non-user workflows."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TenantScope(BaseModel):
    """Minimal tenant identity for internal jobs and workers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: UUID
