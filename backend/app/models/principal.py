"""The authenticated caller.

Every service and repository method takes a ``Principal``. There is no code path
that reads data without one. That is the most important invariant in the
codebase: it makes tenant isolation a property of the type system rather than of
everyone's memory.

Phase 1 builds this from a development header. Phase 2 builds it from a verified
Cognito JWT. Nothing downstream of this module changes when that swap happens —
which is precisely why it is introduced now rather than later.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import ConfidentialityLevel, Role

# Least to most restricted. A caller may read everything at or below clearance.
_CLEARANCE_ORDER: tuple[ConfidentialityLevel, ...] = (
    ConfidentialityLevel.PUBLIC,
    ConfidentialityLevel.INTERNAL,
    ConfidentialityLevel.CONFIDENTIAL,
    ConfidentialityLevel.RESTRICTED,
)

_ROLE_CLEARANCE: dict[Role, ConfidentialityLevel] = {
    Role.ANALYST: ConfidentialityLevel.INTERNAL,
    Role.MANAGER: ConfidentialityLevel.CONFIDENTIAL,
    Role.ADMIN: ConfidentialityLevel.RESTRICTED,
}


class Principal(BaseModel):
    """Immutable identity derived from a verified token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID
    organization_id: UUID
    role: Role
    email: str
    department: str | None = None

    @property
    def clearance(self) -> ConfidentialityLevel:
        return _ROLE_CLEARANCE[self.role]

    @property
    def visible_confidentiality_levels(self) -> tuple[ConfidentialityLevel, ...]:
        """The retrieval filter. Built from the token, never from user input."""
        ceiling = _CLEARANCE_ORDER.index(self.clearance)
        return _CLEARANCE_ORDER[: ceiling + 1]

    def can_read(self, level: ConfidentialityLevel) -> bool:
        return level in self.visible_confidentiality_levels
