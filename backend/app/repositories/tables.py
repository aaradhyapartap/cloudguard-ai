"""ORM tables.

Phase 1 defines only what the foundation needs: Organization, User, Document.
Phase 3 extends Document and adds DocumentChunk; Phase 5 adds Compliance Frameworks,
Controls, Assessments, EvidenceReferences, and AssessmentScoreSnapshots.

Two conventions applied to every tenant-owned table:

* ``organization_id`` is NOT NULL and indexed. It is the RLS predicate, so it is
  on the hot path of literally every query.
* Timestamps are ``timezone=True`` and default to ``now()`` in the **database**,
  not in Python. Application clocks disagree; the database clock is the one that
  orders an audit trail.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enums import (
    ApprovalDecision,
    ApprovalStatus,
    AssessmentStatus,
    ConfidentialityLevel,
    ControlStatus,
    DocumentType,
    ProcessingStatus,
    RiskClassification,
    Role,
)
from app.repositories.database import Base


def _pg_enum(python_enum: type, name: str) -> ENUM:
    """Native PostgreSQL enum.

    A CHECK-constrained text column would also work. A real enum type is chosen
    because an invalid value becomes impossible to insert rather than merely
    discouraged, and because the type is self-documenting in ``\\d+`` output.
    """
    return ENUM(
        python_enum,
        name=name,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
        create_type=False,  # migrations own type creation
    )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    settings: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
        UniqueConstraint("organization_id", "id", name="uq_users_org_id"),
        Index("ix_users_organization_id", "organization_id"),
    )

    # Equal to the Cognito `sub` claim. Using the IdP's identifier as the primary
    # key removes an entire class of "which id is this?" bug at the auth boundary.
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(_pg_enum(Role, "role"), nullable=False)
    department: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_documents_org_id"),
        Index("ix_documents_org_status", "organization_id", "processing_status"),
        Index("ix_documents_org_type", "organization_id", "document_type"),
        Index("ix_documents_org_created", "organization_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")

    document_type: Mapped[DocumentType] = mapped_column(
        _pg_enum(DocumentType, "document_type"),
        nullable=False,
        server_default=DocumentType.UNKNOWN.value,
    )
    confidentiality_level: Mapped[ConfidentialityLevel] = mapped_column(
        _pg_enum(ConfidentialityLevel, "confidentiality_level"),
        nullable=False,
        server_default=ConfidentialityLevel.INTERNAL.value,
    )
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        _pg_enum(ProcessingStatus, "processing_status"),
        nullable=False,
        server_default=ProcessingStatus.QUEUED.value,
    )
    processing_error: Mapped[str | None] = mapped_column(Text)

    uploader_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    department: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str | None] = mapped_column(String(200))
    tags: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="[]")

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_document_chunks_org_id"),
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
        Index(
            "ix_document_chunks_org_document",
            "organization_id",
            "document_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(
        nullable=False,
        server_default="0",
    )
    chunk_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default="{}",
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )


# -----------------------------------------------------------------------------
# Phase 5: Compliance Frameworks, Controls, Assessments & Scoring
# -----------------------------------------------------------------------------


class ComplianceFramework(Base):
    __tablename__ = "compliance_frameworks"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_compliance_frameworks_code_version"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ComplianceControl(Base):
    __tablename__ = "compliance_controls"
    __table_args__ = (
        UniqueConstraint(
            "framework_id",
            "control_code",
            name="uq_compliance_controls_framework_code",
        ),
        Index("ix_compliance_controls_framework_id", "framework_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    framework_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("compliance_frameworks.id", ondelete="CASCADE"),
        nullable=False,
    )
    control_code: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    category: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    default_weight: Mapped[float] = mapped_column(
        Numeric(3, 1), nullable=False, server_default="1.0"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ComplianceAssessment(Base):
    __tablename__ = "compliance_assessments"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_compliance_assessments_org_id"),
        Index("ix_compliance_assessments_org_status", "organization_id", "status"),
        Index("ix_compliance_assessments_org_framework", "organization_id", "framework_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    framework_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("compliance_frameworks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[AssessmentStatus] = mapped_column(
        _pg_enum(AssessmentStatus, "assessment_status"),
        nullable=False,
        server_default=AssessmentStatus.DRAFT.value,
    )
    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    risk_classification: Mapped[RiskClassification] = mapped_column(
        _pg_enum(RiskClassification, "risk_classification"),
        nullable=False,
        server_default=RiskClassification.NOT_SCORED.value,
    )
    scoring_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ControlAssessment(Base):
    __tablename__ = "control_assessments"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_control_assessments_org_id"),
        UniqueConstraint(
            "assessment_id", "control_id", name="uq_control_assessments_assessment_control"
        ),
        ForeignKeyConstraint(
            ["organization_id", "assessment_id"],
            ["compliance_assessments.organization_id", "compliance_assessments.id"],
            ondelete="CASCADE",
            name="fk_control_assessments_assessment_org",
        ),
        Index("ix_control_assessments_org_assessment", "organization_id", "assessment_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    assessment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    control_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("compliance_controls.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[ControlStatus] = mapped_column(
        _pg_enum(ControlStatus, "control_status"),
        nullable=False,
        server_default=ControlStatus.UNASSESSED.value,
    )
    effective_weight: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), nullable=False, server_default="1.0"
    )
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EvidenceReference(Base):
    __tablename__ = "evidence_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "control_assessment_id"],
            ["control_assessments.organization_id", "control_assessments.id"],
            ondelete="CASCADE",
            name="fk_evidence_references_control_assessment_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_evidence_references_document_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "chunk_id"],
            ["document_chunks.organization_id", "document_chunks.id"],
            ondelete="SET NULL (chunk_id)",
            name="fk_evidence_references_chunk_org",
        ),
        Index(
            "ix_evidence_references_org_control",
            "organization_id",
            "control_assessment_id",
        ),
        Index("ix_evidence_references_org_document", "organization_id", "document_id"),
        Index(
            "uq_evidence_references_control_doc_chunk",
            "organization_id",
            "control_assessment_id",
            "document_id",
            "chunk_id",
            unique=True,
            postgresql_where=text("chunk_id IS NOT NULL"),
        ),
        Index(
            "uq_evidence_references_control_doc_nochunk",
            "organization_id",
            "control_assessment_id",
            "document_id",
            unique=True,
            postgresql_where=text("chunk_id IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    control_assessment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    chunk_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    confidentiality_level: Mapped[ConfidentialityLevel] = mapped_column(
        _pg_enum(ConfidentialityLevel, "confidentiality_level"),
        nullable=False,
        server_default=ConfidentialityLevel.INTERNAL.value,
    )
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class AssessmentScoreSnapshot(Base):
    __tablename__ = "assessment_score_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "revision_number",
            name="uq_assessment_score_snapshots_assessment_rev",
        ),
        UniqueConstraint(
            "assessment_id",
            "revision_number",
            "id",
            name="uq_assessment_snapshots_assessment_rev_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "assessment_id"],
            ["compliance_assessments.organization_id", "compliance_assessments.id"],
            ondelete="CASCADE",
            name="fk_assessment_score_snapshots_assessment_org",
        ),
        Index(
            "ix_assessment_score_snapshots_org_assessment",
            "organization_id",
            "assessment_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    assessment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(nullable=False, server_default="1")
    scoring_version: Mapped[str] = mapped_column(String(40), nullable=False)
    framework_version: Mapped[str] = mapped_column(String(40), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    raw_scores: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    risk_classification: Mapped[RiskClassification] = mapped_column(
        _pg_enum(RiskClassification, "risk_classification"),
        nullable=False,
    )
    computed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    computed_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class ScoreOverride(Base):
    __tablename__ = "score_overrides"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "assessment_id"],
            ["compliance_assessments.organization_id", "compliance_assessments.id"],
            ondelete="CASCADE",
            name="fk_score_overrides_assessment_org",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "source_revision_number", "snapshot_id"],
            [
                "assessment_score_snapshots.assessment_id",
                "assessment_score_snapshots.revision_number",
                "assessment_score_snapshots.id",
            ],
            ondelete="RESTRICT",
            name="fk_score_overrides_snapshot_triple",
        ),
        Index("ix_score_overrides_org_assessment", "organization_id", "assessment_id"),
        Index(
            "ix_score_overrides_org_assessment_created",
            "organization_id",
            "assessment_id",
            "overridden_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    assessment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    source_revision_number: Mapped[int] = mapped_column(nullable=False)
    original_overall_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    original_risk_classification: Mapped[RiskClassification] = mapped_column(
        _pg_enum(RiskClassification, "risk_classification"),
        nullable=False,
    )
    override_overall_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    override_risk_classification: Mapped[RiskClassification] = mapped_column(
        _pg_enum(RiskClassification, "risk_classification"),
        nullable=False,
    )
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    overridden_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    overridden_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

class Approval(Base):
    """Tenant-owned durable human approval state.

    ``task_token`` is persistence-internal and must never be exposed through
    public approval projections.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "approver_id"],
            ["users.organization_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_approvals_approver_org",
        ),
        UniqueConstraint(
            "task_token",
            name="uq_approvals_task_token",
        ),
        UniqueConstraint(
            "organization_id",
            "workflow_execution_id",
            name="uq_approvals_org_workflow_execution",
        ),
        Index(
            "ix_approvals_org_id",
            "organization_id",
            "id",
        ),
        Index(
            "ix_approvals_pending_queue",
            "organization_id",
            "created_at",
            postgresql_where=text("decision IS NULL"),
        ),
        Index(
            "ix_approvals_decided_lookup",
            "organization_id",
            "decision",
            "decided_at",
            postgresql_where=text("decision IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_execution_id: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
    )
    recommendation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    proposed_action: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    evidence: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    score_context: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )
    agent_trace_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    generator_model_id: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )
    reviewer_model_id: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )

    status: Mapped[ApprovalStatus] = mapped_column(
        _pg_enum(ApprovalStatus, "approval_status"),
        nullable=False,
        server_default=ApprovalStatus.PENDING.value,
    )
    decision: Mapped[ApprovalDecision | None] = mapped_column(
        _pg_enum(ApprovalDecision, "approval_decision"),
        nullable=True,
    )

    approver_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    modified_action: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )

    task_token: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
