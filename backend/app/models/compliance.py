"""Domain models for compliance evaluation and deterministic risk scoring."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.common import DomainModel
from app.models.enums import (
    AssessmentStatus,
    ConfidentialityLevel,
    ControlStatus,
    RiskClassification,
)


class ControlScoringInput(BaseModel):
    """Immutable input to the deterministic scoring engine for a single control."""

    model_config = ConfigDict(frozen=True)

    control_id: str
    status: ControlStatus
    effective_weight: Decimal = Field(
        default=Decimal("1.0"),
        ge=Decimal("1.0"),
        le=Decimal("5.0"),
        description="Weight bounded between 1.0 and 5.0",
    )
    evidence_count: int = Field(
        default=0,
        ge=0,
        description="Number of validated, persisted evidence references attached to this control",
    )

    @field_validator("effective_weight")
    @classmethod
    def validate_finite_weight(cls, v: Decimal) -> Decimal:
        if v.is_nan() or v.is_infinite():
            raise ValueError(f"effective_weight must be a finite decimal, got {v}")
        if v < Decimal("1.0") or v > Decimal("5.0"):
            raise ValueError(f"effective_weight must be between 1.0 and 5.0, got {v}")
        return v


class ControlScoreOutput(BaseModel):
    """Deterministic score breakdown for an individual control."""

    model_config = ConfigDict(frozen=True)

    control_id: str
    status: ControlStatus
    effective_weight: Decimal
    is_applicable: bool
    evidence_count: int
    is_grounded: bool
    raw_score: Decimal = Field(
        description="Unweighted score 0..100 including grounding penalty if ungrounded",
    )
    weighted_score: Decimal = Field(
        description="raw_score * effective_weight",
    )


class AssessmentScoringInput(BaseModel):
    """Complete collection of control inputs to evaluate against a compliance framework."""

    model_config = ConfigDict(frozen=True)

    framework_id: str
    framework_version: str
    controls: list[ControlScoringInput]
    scoring_version: str = "v1.0"

    @field_validator("controls")
    @classmethod
    def validate_unique_control_ids(
        cls, v: list[ControlScoringInput]
    ) -> list[ControlScoringInput]:
        seen: set[str] = set()
        duplicates: list[str] = []
        for c in v:
            if c.control_id in seen:
                duplicates.append(c.control_id)
            seen.add(c.control_id)
        if duplicates:
            raise ValueError(f"Duplicate control_id values found: {duplicates}")
        return v


class AssessmentScoreResult(BaseModel):
    """Authoritative output of deterministic compliance calculation."""

    model_config = ConfigDict(frozen=True)

    scoring_version: str
    framework_id: str
    framework_version: str
    applicable_control_count: int
    total_control_count: int
    overall_score: Decimal | None = Field(
        default=None,
        description="Aggregated compliance score (0.00..100.00) or None if zero controls",
    )
    residual_risk: Decimal | None = Field(
        default=None,
        description="100.00 - overall_score or None if unassessed/not-applicable",
    )
    risk_classification: RiskClassification
    critical_override_triggered: bool = False
    control_scores: dict[str, ControlScoreOutput] = Field(default_factory=dict)
    raw_scores: dict[str, Decimal] = Field(default_factory=dict)
    component_breakdown: dict[str, Any] = Field(default_factory=dict)


# -----------------------------------------------------------------------------
# Domain Representation & Request / Response Models
# -----------------------------------------------------------------------------


class ComplianceFrameworkRead(DomainModel):
    """Framework metadata (global catalog item)."""

    id: UUID
    code: str
    name: str
    version: str
    description: str
    created_at: datetime
    updated_at: datetime


class ComplianceControlRead(DomainModel):
    """Control definition belonging to a framework."""

    id: UUID
    framework_id: UUID
    control_code: str
    title: str
    description: str
    category: str
    default_weight: Decimal
    created_at: datetime
    updated_at: datetime


class ComplianceAssessmentCreateRequest(DomainModel):
    """Client request to initialize a compliance assessment."""

    framework_id: UUID
    title: str = Field(min_length=1, max_length=256)


class ComplianceAssessmentResponse(DomainModel):
    """Tenant-scoped compliance assessment representation."""

    id: UUID
    organization_id: UUID
    framework_id: UUID
    title: str
    status: AssessmentStatus
    overall_score: Decimal | None
    risk_classification: RiskClassification
    scoring_version: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class ControlAssessmentResponse(DomainModel):
    """Assessment of a single control within a compliance assessment."""

    id: UUID
    organization_id: UUID
    assessment_id: UUID
    control_id: UUID
    status: ControlStatus
    effective_weight: Decimal
    rationale: str | None
    created_at: datetime
    updated_at: datetime
    evidence_count: int = 0


class ControlAssessmentUpdateRequest(DomainModel):
    """Request to update control assessment inputs before finalization."""

    status: ControlStatus | None = None
    effective_weight: Decimal | None = None
    rationale: str | None = None

    @field_validator("effective_weight")
    @classmethod
    def validate_weight(cls, v: Decimal | None) -> Decimal | None:
        if v is not None:
            if v.is_nan() or v.is_infinite():
                raise ValueError(f"effective_weight must be a finite decimal, got {v}")
            if v < Decimal("1.0") or v > Decimal("5.0"):
                raise ValueError(f"effective_weight must be between 1.0 and 5.0, got {v}")
        return v


class EvidenceReferenceCreateRequest(DomainModel):
    """Client request to admit an evidence reference to a control assessment."""

    document_id: UUID
    chunk_id: UUID | None = None


class EvidenceReferenceResponse(DomainModel):
    """Validated evidence reference attached to a control assessment."""

    id: UUID
    organization_id: UUID
    control_assessment_id: UUID
    document_id: UUID
    chunk_id: UUID | None
    confidentiality_level: ConfidentialityLevel
    snippet: str | None
    created_by: UUID
    created_at: datetime


class AssessmentScoreSnapshotResponse(DomainModel):
    """Immutable audit snapshot of an assessment calculation."""

    id: UUID
    organization_id: UUID
    assessment_id: UUID
    revision_number: int
    scoring_version: str
    framework_version: str
    input_snapshot: dict[str, Any]
    raw_scores: dict[str, Any]
    overall_score: Decimal | None
    risk_classification: RiskClassification
    computed_by: UUID | None
    computed_at: datetime
