"""Unit tests for ComplianceAssessmentService."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.models.compliance import (
    AssessmentScoreSnapshotResponse,
    ComplianceAssessmentCreateRequest,
    ComplianceAssessmentResponse,
    ComplianceControlRead,
    ComplianceFrameworkRead,
    ControlAssessmentResponse,
    ControlAssessmentUpdateRequest,
    EvidenceReferenceCreateRequest,
    EvidenceReferenceResponse,
)
from app.models.enums import (
    AssessmentStatus,
    ConfidentialityLevel,
    ControlStatus,
    RiskClassification,
    Role,
)
from app.models.principal import Principal
from app.ports.compliance_repository import ComplianceRepository
from app.services.compliance import ComplianceAssessmentService
from app.services.compliance_scoring import RiskScoringEngine

ORG_ID = uuid4()
USER_ANALYST = uuid4()
USER_MANAGER = uuid4()
USER_ADMIN = uuid4()
FRAMEWORK_ID = uuid4()
CONTROL_1_ID = uuid4()
CONTROL_2_ID = uuid4()

ANALYST_PRINCIPAL = Principal(
    user_id=USER_ANALYST,
    organization_id=ORG_ID,
    role=Role.ANALYST,
    email="analyst@test.local",
)

MANAGER_PRINCIPAL = Principal(
    user_id=USER_MANAGER,
    organization_id=ORG_ID,
    role=Role.MANAGER,
    email="manager@test.local",
)

ADMIN_PRINCIPAL = Principal(
    user_id=USER_ADMIN,
    organization_id=ORG_ID,
    role=Role.ADMIN,
    email="admin@test.local",
)


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock(spec=ComplianceRepository)

    # Default framework & controls
    repo.get_framework.return_value = ComplianceFrameworkRead(
        id=FRAMEWORK_ID,
        code="SOC2",
        name="SOC 2 Type II",
        version="2026.1",
        description="SOC 2 security framework",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo.get_framework_controls.return_value = [
        ComplianceControlRead(
            id=CONTROL_1_ID,
            framework_id=FRAMEWORK_ID,
            control_code="CC6.1",
            title="Logical Access Controls",
            description="Access controls",
            category="Security",
            default_weight=Decimal("3.0"),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        ComplianceControlRead(
            id=CONTROL_2_ID,
            framework_id=FRAMEWORK_ID,
            control_code="CC6.2",
            title="User Registration",
            description="User registration",
            category="Security",
            default_weight=Decimal("5.0"),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
    ]
    repo.lock_assessment = repo.get_assessment
    repo.get_latest_score_override.return_value = None
    repo.get_latest_snapshot.return_value = None
    return repo


@pytest.fixture
def service(mock_repo: AsyncMock) -> ComplianceAssessmentService:
    return ComplianceAssessmentService(
        repository=mock_repo,
        scoring_engine=RiskScoringEngine(),
    )


# -----------------------------------------------------------------------------
# Assessment Creation Tests
# -----------------------------------------------------------------------------


async def test_analyst_can_create_assessment(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    assessment_id = uuid4()
    mock_repo.create_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Q1 SOC2 Assessment",
        status=AssessmentStatus.DRAFT,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    req = ComplianceAssessmentCreateRequest(
        framework_id=FRAMEWORK_ID,
        title="Q1 SOC2 Assessment",
    )
    result = await service.create_assessment(principal=ANALYST_PRINCIPAL, request=req)

    assert result.id == assessment_id
    assert result.status == AssessmentStatus.DRAFT
    mock_repo.create_assessment.assert_called_once()


async def test_manager_can_create_assessment(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    assessment_id = uuid4()
    mock_repo.create_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Manager Assessment",
        status=AssessmentStatus.DRAFT,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_MANAGER,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    req = ComplianceAssessmentCreateRequest(
        framework_id=FRAMEWORK_ID,
        title="Manager Assessment",
    )
    result = await service.create_assessment(principal=MANAGER_PRINCIPAL, request=req)
    assert result.id == assessment_id


async def test_admin_cannot_create_assessment(service: ComplianceAssessmentService) -> None:
    req = ComplianceAssessmentCreateRequest(
        framework_id=FRAMEWORK_ID,
        title="Admin Assessment",
    )
    with pytest.raises(AuthorizationError):
        await service.create_assessment(principal=ADMIN_PRINCIPAL, request=req)


async def test_create_assessment_nonexistent_framework_raises_not_found(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.get_framework.return_value = None
    req = ComplianceAssessmentCreateRequest(
        framework_id=uuid4(),
        title="Unknown Framework Assessment",
    )
    with pytest.raises(NotFoundError):
        await service.create_assessment(principal=ANALYST_PRINCIPAL, request=req)


# -----------------------------------------------------------------------------
# Control Updates Tests
# -----------------------------------------------------------------------------


async def test_update_control_assessment_succeeds(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    ca_id = uuid4()
    assessment_id = uuid4()

    mock_repo.get_control_assessment.return_value = ControlAssessmentResponse(
        id=ca_id,
        organization_id=ORG_ID,
        assessment_id=assessment_id,
        control_id=CONTROL_1_ID,
        status=ControlStatus.UNASSESSED,
        effective_weight=Decimal("3.0"),
        rationale=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Test Assessment",
        status=AssessmentStatus.DRAFT,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.update_control_assessment.return_value = ControlAssessmentResponse(
        id=ca_id,
        organization_id=ORG_ID,
        assessment_id=assessment_id,
        control_id=CONTROL_1_ID,
        status=ControlStatus.SATISFIED,
        effective_weight=Decimal("4.0"),
        rationale="Implemented SSO and MFA",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    update_req = ControlAssessmentUpdateRequest(
        status=ControlStatus.SATISFIED,
        effective_weight=Decimal("4.0"),
        rationale="Implemented SSO and MFA",
    )
    updated = await service.update_control_assessment(
        principal=ANALYST_PRINCIPAL,
        control_assessment_id=ca_id,
        request=update_req,
    )
    assert updated.status == ControlStatus.SATISFIED
    assert updated.effective_weight == Decimal("4.0")


async def test_update_control_assessment_on_completed_assessment_fails(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    ca_id = uuid4()
    assessment_id = uuid4()

    mock_repo.get_control_assessment.return_value = ControlAssessmentResponse(
        id=ca_id,
        organization_id=ORG_ID,
        assessment_id=assessment_id,
        control_id=CONTROL_1_ID,
        status=ControlStatus.SATISFIED,
        effective_weight=Decimal("3.0"),
        rationale=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Completed Assessment",
        status=AssessmentStatus.COMPLETED,
        overall_score=Decimal("85.00"),
        risk_classification=RiskClassification.LOW,
        scoring_version="v1.0",
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    update_req = ControlAssessmentUpdateRequest(
        status=ControlStatus.DEFICIENT,
    )
    with pytest.raises(ConflictError):
        await service.update_control_assessment(
            principal=ANALYST_PRINCIPAL,
            control_assessment_id=ca_id,
            request=update_req,
        )


# -----------------------------------------------------------------------------
# Evidence Admission & Clearance Tests
# -----------------------------------------------------------------------------


async def test_analyst_cannot_admit_evidence_above_clearance(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    ca_id = uuid4()
    assessment_id = uuid4()
    doc_id = uuid4()

    mock_repo.get_control_assessment.return_value = ControlAssessmentResponse(
        id=ca_id,
        organization_id=ORG_ID,
        assessment_id=assessment_id,
        control_id=CONTROL_1_ID,
        status=ControlStatus.UNASSESSED,
        effective_weight=Decimal("3.0"),
        rationale=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Test Assessment",
        status=AssessmentStatus.DRAFT,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    # Document has CONFIDENTIAL level (Analyst clearance is INTERNAL only)
    mock_repo.get_document_for_evidence.return_value = (
        doc_id,
        ConfidentialityLevel.CONFIDENTIAL,
    )

    req = EvidenceReferenceCreateRequest(
        document_id=doc_id,
    )
    with pytest.raises(AuthorizationError):
        await service.admit_evidence(
            principal=ANALYST_PRINCIPAL,
            control_assessment_id=ca_id,
            request=req,
        )


async def test_manager_can_admit_confidential_evidence(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    ca_id = uuid4()
    assessment_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()
    ev_id = uuid4()

    mock_repo.get_control_assessment.return_value = ControlAssessmentResponse(
        id=ca_id,
        organization_id=ORG_ID,
        assessment_id=assessment_id,
        control_id=CONTROL_1_ID,
        status=ControlStatus.UNASSESSED,
        effective_weight=Decimal("3.0"),
        rationale=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Test Assessment",
        status=AssessmentStatus.DRAFT,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_MANAGER,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_document_for_evidence.return_value = (
        doc_id,
        ConfidentialityLevel.CONFIDENTIAL,
    )
    mock_repo.get_chunk_for_evidence.return_value = (
        chunk_id,
        doc_id,
        "Confidential chunk content",
    )
    mock_repo.add_evidence_reference.return_value = EvidenceReferenceResponse(
        id=ev_id,
        organization_id=ORG_ID,
        control_assessment_id=ca_id,
        document_id=doc_id,
        chunk_id=chunk_id,
        confidentiality_level=ConfidentialityLevel.CONFIDENTIAL,
        snippet="Confidential chunk content",
        created_by=USER_MANAGER,
        created_at=datetime.now(UTC),
    )

    req = EvidenceReferenceCreateRequest(
        document_id=doc_id,
        chunk_id=chunk_id,
    )
    ev = await service.admit_evidence(
        principal=MANAGER_PRINCIPAL,
        control_assessment_id=ca_id,
        request=req,
    )
    assert ev.id == ev_id
    assert ev.confidentiality_level == ConfidentialityLevel.CONFIDENTIAL


async def test_evidence_admission_with_mismatched_chunk_fails(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    ca_id = uuid4()
    assessment_id = uuid4()
    doc_id = uuid4()
    other_doc_id = uuid4()
    chunk_id = uuid4()

    mock_repo.get_control_assessment.return_value = ControlAssessmentResponse(
        id=ca_id,
        organization_id=ORG_ID,
        assessment_id=assessment_id,
        control_id=CONTROL_1_ID,
        status=ControlStatus.UNASSESSED,
        effective_weight=Decimal("3.0"),
        rationale=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Test Assessment",
        status=AssessmentStatus.DRAFT,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_document_for_evidence.return_value = (
        doc_id,
        ConfidentialityLevel.INTERNAL,
    )
    # Chunk belongs to other_doc_id
    mock_repo.get_chunk_for_evidence.return_value = (
        chunk_id,
        other_doc_id,
        "Mismatched chunk",
    )

    req = EvidenceReferenceCreateRequest(
        document_id=doc_id,
        chunk_id=chunk_id,
    )
    with pytest.raises(ValidationError):
        await service.admit_evidence(
            principal=ANALYST_PRINCIPAL,
            control_assessment_id=ca_id,
            request=req,
        )


async def test_evidence_snippet_provenance_from_chunk_or_none(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    ca_id = uuid4()
    assessment_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    mock_repo.get_control_assessment.return_value = ControlAssessmentResponse(
        id=ca_id,
        organization_id=ORG_ID,
        assessment_id=assessment_id,
        control_id=CONTROL_1_ID,
        status=ControlStatus.UNASSESSED,
        effective_weight=Decimal("3.0"),
        rationale=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Test Assessment",
        status=AssessmentStatus.DRAFT,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_document_for_evidence.return_value = (
        doc_id,
        ConfidentialityLevel.INTERNAL,
    )
    mock_repo.get_chunk_for_evidence.return_value = (
        chunk_id,
        doc_id,
        "Trusted chunk text excerpt",
    )
    mock_repo.add_evidence_reference.return_value = EvidenceReferenceResponse(
        id=uuid4(),
        organization_id=ORG_ID,
        control_assessment_id=ca_id,
        document_id=doc_id,
        chunk_id=chunk_id,
        confidentiality_level=ConfidentialityLevel.INTERNAL,
        snippet="Trusted chunk text excerpt",
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
    )

    # 1. With chunk_id -> snippet is extracted from chunk persistence
    req_with_chunk = EvidenceReferenceCreateRequest(
        document_id=doc_id,
        chunk_id=chunk_id,
    )
    ev = await service.admit_evidence(
        principal=ANALYST_PRINCIPAL,
        control_assessment_id=ca_id,
        request=req_with_chunk,
    )
    assert ev.snippet == "Trusted chunk text excerpt"
    mock_repo.add_evidence_reference.assert_called_with(
        organization_id=ORG_ID,
        control_assessment_id=ca_id,
        document_id=doc_id,
        chunk_id=chunk_id,
        confidentiality_level=ConfidentialityLevel.INTERNAL,
        snippet="Trusted chunk text excerpt",
        created_by=USER_ANALYST,
        touch_assessment=True,
    )

    # 2. Without chunk_id -> snippet is None
    req_without_chunk = EvidenceReferenceCreateRequest(
        document_id=doc_id,
        chunk_id=None,
    )
    await service.admit_evidence(
        principal=ANALYST_PRINCIPAL,
        control_assessment_id=ca_id,
        request=req_without_chunk,
    )
    mock_repo.add_evidence_reference.assert_called_with(
        organization_id=ORG_ID,
        control_assessment_id=ca_id,
        document_id=doc_id,
        chunk_id=None,
        confidentiality_level=ConfidentialityLevel.INTERNAL,
        snippet=None,
        created_by=USER_ANALYST,
        touch_assessment=True,
    )


async def test_duplicate_evidence_admission_raises_conflict(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    ca_id = uuid4()
    assessment_id = uuid4()
    doc_id = uuid4()

    mock_repo.get_control_assessment.return_value = ControlAssessmentResponse(
        id=ca_id,
        organization_id=ORG_ID,
        assessment_id=assessment_id,
        control_id=CONTROL_1_ID,
        status=ControlStatus.UNASSESSED,
        effective_weight=Decimal("3.0"),
        rationale=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Test Assessment",
        status=AssessmentStatus.DRAFT,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_document_for_evidence.return_value = (
        doc_id,
        ConfidentialityLevel.INTERNAL,
    )
    mock_repo.add_evidence_reference.side_effect = ConflictError(
        "This evidence reference has already been attached to the control."
    )

    req = EvidenceReferenceCreateRequest(
        document_id=doc_id,
    )
    with pytest.raises(ConflictError):
        await service.admit_evidence(
            principal=ANALYST_PRINCIPAL,
            control_assessment_id=ca_id,
            request=req,
        )


# -----------------------------------------------------------------------------
# Score Computation & Actor Independence Tests
# -----------------------------------------------------------------------------


async def test_score_computation_and_actor_independence(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    assessment_id = uuid4()
    ca1_id = uuid4()
    ca2_id = uuid4()

    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Actor Independence Test Assessment",
        status=AssessmentStatus.IN_PROGRESS,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_control_assessments.return_value = [
        ControlAssessmentResponse(
            id=ca1_id,
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("3.0"),
            rationale="Satisfied with evidence",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            evidence_count=1,
        ),
        ControlAssessmentResponse(
            id=ca2_id,
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            control_id=CONTROL_2_ID,
            status=ControlStatus.PARTIALLY_SATISFIED,
            effective_weight=Decimal("5.0"),
            rationale="Partially satisfied with evidence",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            evidence_count=1,
        ),
    ]
    mock_repo.get_evidence_references_for_assessment.return_value = [
        EvidenceReferenceResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            control_assessment_id=ca1_id,
            document_id=uuid4(),
            chunk_id=None,
            confidentiality_level=ConfidentialityLevel.INTERNAL,
            snippet="Policy snippet",
            created_by=USER_ANALYST,
            created_at=datetime.now(UTC),
        ),
        EvidenceReferenceResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            control_assessment_id=ca2_id,
            document_id=uuid4(),
            chunk_id=None,
            confidentiality_level=ConfidentialityLevel.CONFIDENTIAL,
            snippet="Confidential evidence",
            created_by=USER_MANAGER,
            created_at=datetime.now(UTC),
        ),
    ]
    mock_repo.save_score_snapshot_and_update_assessment.return_value = (
        AssessmentScoreSnapshotResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("68.75"),
            risk_classification=RiskClassification.MEDIUM,
            computed_by=USER_ANALYST,
            computed_at=datetime.now(UTC),
        )
    )

    # 1. Analyst computes score
    analyst_result = await service.compute_score(
        principal=ANALYST_PRINCIPAL, assessment_id=assessment_id
    )

    # 2. Manager computes score
    mock_repo.save_score_snapshot_and_update_assessment.return_value = (
        AssessmentScoreSnapshotResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            revision_number=2,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("68.75"),
            risk_classification=RiskClassification.MEDIUM,
            computed_by=USER_MANAGER,
            computed_at=datetime.now(UTC),
        )
    )
    manager_result = await service.compute_score(
        principal=MANAGER_PRINCIPAL, assessment_id=assessment_id
    )

    # Mathematical outputs must be identical
    assert analyst_result.overall_score == manager_result.overall_score
    assert analyst_result.residual_risk == manager_result.residual_risk
    assert analyst_result.risk_classification == manager_result.risk_classification
    assert analyst_result.raw_scores == manager_result.raw_scores
    assert analyst_result.scoring_version == manager_result.scoring_version


async def test_admin_cannot_compute_score(service: ComplianceAssessmentService) -> None:
    with pytest.raises(AuthorizationError):
        await service.compute_score(
            principal=ADMIN_PRINCIPAL, assessment_id=uuid4()
        )


async def test_score_computation_with_zero_applicable_controls(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    assessment_id = uuid4()
    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Zero Applicable Assessment",
        status=AssessmentStatus.IN_PROGRESS,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    # Both controls are NOT_APPLICABLE
    mock_repo.get_control_assessments.return_value = [
        ControlAssessmentResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.NOT_APPLICABLE,
            effective_weight=Decimal("3.0"),
            rationale="Not applicable",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            evidence_count=0,
        ),
        ControlAssessmentResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            control_id=CONTROL_2_ID,
            status=ControlStatus.NOT_APPLICABLE,
            effective_weight=Decimal("5.0"),
            rationale="Not applicable",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            evidence_count=0,
        ),
    ]
    mock_repo.get_evidence_references_for_assessment.return_value = []
    mock_repo.save_score_snapshot_and_update_assessment.return_value = (
        AssessmentScoreSnapshotResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=None,
            risk_classification=RiskClassification.NOT_SCORED,
            computed_by=USER_ANALYST,
            computed_at=datetime.now(UTC),
        )
    )

    result = await service.compute_score(
        principal=ANALYST_PRINCIPAL, assessment_id=assessment_id
    )
    assert result.overall_score is None
    assert result.residual_risk is None
    assert result.risk_classification == RiskClassification.NOT_SCORED
    assert result.applicable_control_count == 0


async def test_score_computation_with_critical_override(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    assessment_id = uuid4()
    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Critical Override Assessment",
        status=AssessmentStatus.IN_PROGRESS,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    # Control 1 (weight 3.0) satisfied, Control 2 (weight 5.0) deficient ->
    # triggers critical override
    mock_repo.get_control_assessments.return_value = [
        ControlAssessmentResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("3.0"),
            rationale="Satisfied with evidence",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            evidence_count=1,
        ),
        ControlAssessmentResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            control_id=CONTROL_2_ID,
            status=ControlStatus.DEFICIENT,
            effective_weight=Decimal("5.0"),
            rationale="Critical requirement missing",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            evidence_count=0,
        ),
    ]
    mock_repo.get_evidence_references_for_assessment.return_value = []
    mock_repo.save_score_snapshot_and_update_assessment.return_value = (
        AssessmentScoreSnapshotResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("37.50"),
            risk_classification=RiskClassification.CRITICAL,
            computed_by=USER_ANALYST,
            computed_at=datetime.now(UTC),
        )
    )

    result = await service.compute_score(
        principal=ANALYST_PRINCIPAL, assessment_id=assessment_id
    )
    assert result.critical_override_triggered is True
    assert result.risk_classification == RiskClassification.CRITICAL


async def test_admin_can_read_assessments_and_snapshots(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    assessment_id = uuid4()
    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Admin Read Assessment",
        status=AssessmentStatus.IN_PROGRESS,
        overall_score=Decimal("75.00"),
        risk_classification=RiskClassification.MEDIUM,
        scoring_version="v1.0",
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.list_assessments.return_value = [mock_repo.get_assessment.return_value]
    mock_repo.list_snapshots.return_value = [
        AssessmentScoreSnapshotResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("75.00"),
            risk_classification=RiskClassification.MEDIUM,
            computed_by=USER_ANALYST,
            computed_at=datetime.now(UTC),
        )
    ]

    # Admin holds RISK_READ
    assessment = await service.get_assessment(
        principal=ADMIN_PRINCIPAL, assessment_id=assessment_id
    )
    assert assessment.id == assessment_id

    assessments = await service.list_assessments(principal=ADMIN_PRINCIPAL)
    assert len(assessments) == 1

    snapshots = await service.list_snapshots(
        principal=ADMIN_PRINCIPAL, assessment_id=assessment_id
    )
    assert len(snapshots) == 1


async def test_score_snapshot_records_exact_evidence_ids_and_no_snippets(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    assessment_id = uuid4()
    ca1_id = uuid4()
    ca2_id = uuid4()
    ev1_id = uuid4()
    ev2_id = uuid4()
    ev3_id = uuid4()

    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Evidence Snapshot Test",
        status=AssessmentStatus.IN_PROGRESS,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.get_control_assessments.return_value = [
        ControlAssessmentResponse(
            id=ca1_id,
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("3.0"),
            rationale="MFA Policy and Architecture",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            evidence_count=2,
        ),
        ControlAssessmentResponse(
            id=ca2_id,
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            control_id=CONTROL_2_ID,
            status=ControlStatus.PARTIALLY_SATISFIED,
            effective_weight=Decimal("5.0"),
            rationale="Badge access partially active",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            evidence_count=1,
        ),
    ]
    mock_repo.get_evidence_references_for_assessment.return_value = [
        EvidenceReferenceResponse(
            id=ev1_id,
            organization_id=ORG_ID,
            control_assessment_id=ca1_id,
            document_id=uuid4(),
            chunk_id=uuid4(),
            confidentiality_level=ConfidentialityLevel.INTERNAL,
            snippet="SENSITIVE_SNIPPET_MFA_DETAILS_1",
            created_by=USER_ANALYST,
            created_at=datetime.now(UTC),
        ),
        EvidenceReferenceResponse(
            id=ev2_id,
            organization_id=ORG_ID,
            control_assessment_id=ca1_id,
            document_id=uuid4(),
            chunk_id=None,
            confidentiality_level=ConfidentialityLevel.INTERNAL,
            snippet="SENSITIVE_SNIPPET_MFA_DETAILS_2",
            created_by=USER_ANALYST,
            created_at=datetime.now(UTC),
        ),
        EvidenceReferenceResponse(
            id=ev3_id,
            organization_id=ORG_ID,
            control_assessment_id=ca2_id,
            document_id=uuid4(),
            chunk_id=uuid4(),
            confidentiality_level=ConfidentialityLevel.CONFIDENTIAL,
            snippet="SENSITIVE_SNIPPET_PHYSICAL_BADGES",
            created_by=USER_MANAGER,
            created_at=datetime.now(UTC),
        ),
    ]
    mock_repo.save_score_snapshot_and_update_assessment.return_value = (
        AssessmentScoreSnapshotResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("68.75"),
            risk_classification=RiskClassification.MEDIUM,
            computed_by=USER_ANALYST,
            computed_at=datetime.now(UTC),
        )
    )

    result = await service.compute_score(
        principal=ANALYST_PRINCIPAL,
        assessment_id=assessment_id,
    )
    assert result.overall_score == Decimal("68.75")

    # Verify input_snapshot structure passed to repository
    save_args = mock_repo.save_score_snapshot_and_update_assessment.call_args.kwargs
    saved_snapshot = save_args["input_snapshot"]

    assert saved_snapshot["framework_id"] == str(FRAMEWORK_ID)
    assert saved_snapshot["framework_version"] == "2026.1"
    assert saved_snapshot["scoring_version"] == "v1.0"

    controls = saved_snapshot["controls"]
    assert len(controls) == 2

    c1_snap = next(c for c in controls if c["control_id"] == str(CONTROL_1_ID))
    c2_snap = next(c for c in controls if c["control_id"] == str(CONTROL_2_ID))

    assert c1_snap["status"] == "satisfied"
    assert c1_snap["effective_weight"] == "3.0"
    assert c1_snap["evidence_count"] == 2
    assert c1_snap["evidence_reference_ids"] == sorted([str(ev1_id), str(ev2_id)])

    assert c2_snap["status"] == "partially_satisfied"
    assert c2_snap["effective_weight"] == "5.0"
    assert c2_snap["evidence_count"] == 1
    assert c2_snap["evidence_reference_ids"] == [str(ev3_id)]

    # Verify NO snippet text or sensitive material entered the snapshot
    snapshot_str = str(saved_snapshot)
    assert "SENSITIVE_SNIPPET" not in snapshot_str
    assert "snippet" not in snapshot_str
    assert "content" not in snapshot_str


async def test_score_snapshot_ordering_is_deterministic_regardless_of_retrieval_order(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    assessment_id = uuid4()
    ca1_id = uuid4()
    ca2_id = uuid4()
    ev1_id = uuid4()
    ev2_id = uuid4()

    mock_repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Ordering Test",
        status=AssessmentStatus.IN_PROGRESS,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.save_score_snapshot_and_update_assessment.return_value = (
        AssessmentScoreSnapshotResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("68.75"),
            risk_classification=RiskClassification.MEDIUM,
            computed_by=USER_ANALYST,
            computed_at=datetime.now(UTC),
        )
    )

    ca1 = ControlAssessmentResponse(
        id=ca1_id,
        organization_id=ORG_ID,
        assessment_id=assessment_id,
        control_id=CONTROL_1_ID,
        status=ControlStatus.SATISFIED,
        effective_weight=Decimal("3.0"),
        rationale=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        evidence_count=2,
    )
    ca2 = ControlAssessmentResponse(
        id=ca2_id,
        organization_id=ORG_ID,
        assessment_id=assessment_id,
        control_id=CONTROL_2_ID,
        status=ControlStatus.PARTIALLY_SATISFIED,
        effective_weight=Decimal("5.0"),
        rationale=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        evidence_count=0,
    )
    ev1 = EvidenceReferenceResponse(
        id=ev1_id,
        organization_id=ORG_ID,
        control_assessment_id=ca1_id,
        document_id=uuid4(),
        chunk_id=None,
        confidentiality_level=ConfidentialityLevel.INTERNAL,
        snippet="Snippet 1",
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
    )
    ev2 = EvidenceReferenceResponse(
        id=ev2_id,
        organization_id=ORG_ID,
        control_assessment_id=ca1_id,
        document_id=uuid4(),
        chunk_id=None,
        confidentiality_level=ConfidentialityLevel.INTERNAL,
        snippet="Snippet 2",
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
    )

    # Run 1: Normal order
    mock_repo.get_control_assessments.return_value = [ca1, ca2]
    mock_repo.get_evidence_references_for_assessment.return_value = [ev1, ev2]
    await service.compute_score(principal=ANALYST_PRINCIPAL, assessment_id=assessment_id)
    snapshot_run1 = mock_repo.save_score_snapshot_and_update_assessment.call_args.kwargs[
        "input_snapshot"
    ]

    # Run 2: Reversed retrieval order
    mock_repo.get_control_assessments.return_value = [ca2, ca1]
    mock_repo.get_evidence_references_for_assessment.return_value = [ev2, ev1]
    await service.compute_score(principal=ANALYST_PRINCIPAL, assessment_id=assessment_id)
    snapshot_run2 = mock_repo.save_score_snapshot_and_update_assessment.call_args.kwargs[
        "input_snapshot"
    ]

    # Both input snapshots must be completely identical
    assert snapshot_run1 == snapshot_run2
