"""Read-only compliance policy boundary for bounded agent tools."""

from __future__ import annotations

from uuid import UUID

from app.core.errors import NotFoundError, ValidationError
from app.models.agents import PolicyReadResult
from app.models.principal import Principal
from app.ports.compliance_repository import ComplianceRepository
from app.security.authz import Permission, require_permission


class CompliancePolicyReadService:
    """Read framework/control policy context without mutation or scoring."""

    def __init__(
        self,
        *,
        repository: ComplianceRepository,
    ) -> None:
        self._repository = repository

    async def get_policy(
        self,
        *,
        principal: Principal,
        assessment_id: UUID,
        control_ids: list[UUID] | None = None,
    ) -> PolicyReadResult:
        """Return bounded policy context for a tenant-visible assessment."""
        require_permission(principal, Permission.COMPLIANCE_CREATE)

        assessment = await self._repository.get_assessment(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        if assessment is None:
            raise NotFoundError("The requested compliance assessment does not exist.")

        framework = await self._repository.get_framework(assessment.framework_id)
        if framework is None:
            raise NotFoundError(
                "The framework associated with this assessment does not exist."
            )

        controls = await self._repository.get_framework_controls(assessment.framework_id)
        controls_by_id = {control.id: control for control in controls}

        if control_ids is not None:
            requested_ids = set(control_ids)
            unknown_ids = requested_ids - set(controls_by_id)
            if unknown_ids:
                raise ValidationError(
                    "One or more requested controls do not belong to the assessment framework."
                )

            controls = [
                control
                for control in controls
                if control.id in requested_ids
            ]

        if len(controls) > 25:
            raise ValidationError(
                "The policy read exceeds the maximum of 25 controls."
            )

        return PolicyReadResult(
            assessment_id=assessment.id,
            framework=framework,
            controls=controls,
        )
