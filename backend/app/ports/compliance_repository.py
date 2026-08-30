"""Port: persistence operations required for compliance frameworks, assessments, and scoring."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models.compliance import (
    AssessmentScoreSnapshotResponse,
    ComplianceAssessmentResponse,
    ComplianceControlRead,
    ComplianceFrameworkRead,
    ControlAssessmentResponse,
    EvidenceReferenceResponse,
)
from app.models.enums import (
    ConfidentialityLevel,
    ControlStatus,
    RiskClassification,
)


class ComplianceRepository(ABC):
    """Persistence boundary for compliance frameworks, assessments, evidence, and scores."""

    @abstractmethod
    async def get_framework(self, framework_id: UUID) -> ComplianceFrameworkRead | None:
        """Fetch a global compliance framework by ID."""

    @abstractmethod
    async def get_framework_controls(
        self, framework_id: UUID
    ) -> list[ComplianceControlRead]:
        """Fetch all controls defined for a framework, ordered by control_code."""

    @abstractmethod
    async def create_assessment(
        self,
        *,
        organization_id: UUID,
        framework_id: UUID,
        title: str,
        created_by: UUID,
        controls: list[ComplianceControlRead],
    ) -> ComplianceAssessmentResponse:
        """Atomically initialize a new compliance assessment with unassessed control rows."""

    @abstractmethod
    async def get_assessment(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
        for_update: bool = False,
    ) -> ComplianceAssessmentResponse | None:
        """Fetch an assessment scoped to the organization."""

    @abstractmethod
    async def lock_assessment(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> ComplianceAssessmentResponse | None:
        """Acquire a row-level lock (FOR UPDATE) on a compliance assessment within tenant."""

    @abstractmethod
    async def list_assessments(
        self,
        *,
        organization_id: UUID,
        limit: int = 25,
        offset: int = 0,
    ) -> list[ComplianceAssessmentResponse]:
        """List assessments for a tenant."""

    @abstractmethod
    async def get_control_assessment(
        self,
        *,
        organization_id: UUID,
        control_assessment_id: UUID,
    ) -> ControlAssessmentResponse | None:
        """Fetch one control assessment by ID within tenant."""

    @abstractmethod
    async def get_control_assessments(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> list[ControlAssessmentResponse]:
        """Fetch all control assessments for an assessment within tenant."""

    @abstractmethod
    async def update_control_assessment(
        self,
        *,
        organization_id: UUID,
        control_assessment_id: UUID,
        status: ControlStatus | None = None,
        effective_weight: Decimal | None = None,
        rationale: str | None = None,
        touch_assessment: bool = True,
    ) -> ControlAssessmentResponse | None:
        """Update status, weight, or rationale on a control assessment and optionally
        advance DRAFT.
        """

    @abstractmethod
    async def get_document_for_evidence(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
    ) -> tuple[UUID, ConfidentialityLevel] | None:
        """Fetch document ID and confidentiality level within tenant."""

    @abstractmethod
    async def get_chunk_for_evidence(
        self,
        *,
        organization_id: UUID,
        chunk_id: UUID,
    ) -> tuple[UUID, UUID, str] | None:
        """Fetch chunk ID, document ID, and content within tenant."""

    @abstractmethod
    async def add_evidence_reference(
        self,
        *,
        organization_id: UUID,
        control_assessment_id: UUID,
        document_id: UUID,
        chunk_id: UUID | None,
        confidentiality_level: ConfidentialityLevel,
        snippet: str | None,
        created_by: UUID,
        touch_assessment: bool = True,
    ) -> EvidenceReferenceResponse:
        """Persist a validated evidence reference and optionally advance DRAFT."""

    @abstractmethod
    async def get_evidence_references_for_assessment(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> list[EvidenceReferenceResponse]:
        """Fetch all admitted evidence references for an assessment."""

    @abstractmethod
    async def get_evidence_references_for_control(
        self,
        *,
        organization_id: UUID,
        control_assessment_id: UUID,
    ) -> list[EvidenceReferenceResponse]:
        """Fetch all admitted evidence references for a specific control assessment."""

    @abstractmethod
    async def save_score_snapshot_and_update_assessment(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
        scoring_version: str,
        framework_version: str,
        input_snapshot: dict[str, Any],
        raw_scores: dict[str, Any],
        overall_score: Decimal | None,
        risk_classification: RiskClassification,
        computed_by: UUID | None,
    ) -> AssessmentScoreSnapshotResponse:
        """Atomically allocate the next revision number, persist snapshot, and update
        assessment scores.
        """

    @abstractmethod
    async def list_snapshots(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> list[AssessmentScoreSnapshotResponse]:
        """List historical score snapshots for an assessment in ascending revision order."""
