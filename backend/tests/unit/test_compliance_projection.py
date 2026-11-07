"""Unit tests for clearance-safe compliance evidence projection."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.errors import NotFoundError
from app.models.compliance import (
    ComplianceAssessmentResponse,
    ControlAssessmentResponse,
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

ORG_ID = uuid4()
USER_ANALYST = uuid4()
USER_MANAGER = uuid4()
USER_ADMIN = uuid4()
FRAMEWORK_ID = uuid4()
ASSESSMENT_ID = uuid4()
CONTROL_1_ID = uuid4()
CONTROL_2_ID = uuid4()
CA_1_ID = uuid4()
CA_2_ID = uuid4()
EV_INTERNAL_ID = uuid4()
EV_CONFIDENTIAL_ID = uuid4()
EV_RESTRICTED_ID = uuid4()

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

    repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=ASSESSMENT_ID,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Multi-Clearance Assessment",
        status=AssessmentStatus.IN_PROGRESS,
        overall_score=Decimal("75.00"),
        risk_classification=RiskClassification.MEDIUM,
        scoring_version="v1.0",
        created_by=USER_ANALYST,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo.get_latest_score_override.return_value = None

    repo.get_control_assessments.return_value = [
        ControlAssessmentResponse(
            id=CA_1_ID,
            organization_id=ORG_ID,
            assessment_id=ASSESSMENT_ID,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("3.0"),
            rationale="Access control implementation",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            evidence_count=2,
        ),
        ControlAssessmentResponse(
            id=CA_2_ID,
            organization_id=ORG_ID,
            assessment_id=ASSESSMENT_ID,
            control_id=CONTROL_2_ID,
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("5.0"),
            rationale="Executive security policy",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            evidence_count=1,
        ),
    ]

    # Control 1 has INTERNAL + CONFIDENTIAL evidence
    # Control 2 has RESTRICTED evidence
    repo.get_evidence_references_for_assessment.return_value = [
        EvidenceReferenceResponse(
            id=EV_INTERNAL_ID,
            organization_id=ORG_ID,
            control_assessment_id=CA_1_ID,
            document_id=uuid4(),
            chunk_id=uuid4(),
            confidentiality_level=ConfidentialityLevel.INTERNAL,
            snippet="Internal password guidelines",
            created_by=USER_ANALYST,
            created_at=datetime.now(UTC),
        ),
        EvidenceReferenceResponse(
            id=EV_CONFIDENTIAL_ID,
            organization_id=ORG_ID,
            control_assessment_id=CA_1_ID,
            document_id=uuid4(),
            chunk_id=uuid4(),
            confidentiality_level=ConfidentialityLevel.CONFIDENTIAL,
            snippet="Confidential network topology map",
            created_by=USER_MANAGER,
            created_at=datetime.now(UTC),
        ),
        EvidenceReferenceResponse(
            id=EV_RESTRICTED_ID,
            organization_id=ORG_ID,
            control_assessment_id=CA_2_ID,
            document_id=uuid4(),
            chunk_id=uuid4(),
            confidentiality_level=ConfidentialityLevel.RESTRICTED,
            snippet="Restricted executive board security charter",
            created_by=USER_ADMIN,
            created_at=datetime.now(UTC),
        ),
    ]
    return repo


@pytest.fixture
def service(mock_repo: AsyncMock) -> ComplianceAssessmentService:
    return ComplianceAssessmentService(repository=mock_repo)


# -----------------------------------------------------------------------------
# Clearance-Safe Evidence Projection Tests
# -----------------------------------------------------------------------------


async def test_analyst_projection_redacts_confidential_and_restricted_evidence(
    service: ComplianceAssessmentService,
) -> None:
    proj = await service.get_assessment_projection(
        principal=ANALYST_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )

    # 1. Authoritative score is 100% preserved and untouched
    assert proj.overall_score == Decimal("75.00")
    assert proj.risk_classification == RiskClassification.MEDIUM
    assert proj.scoring_version == "v1.0"
    assert proj.any_hidden_evidence is True

    # 2. Control 1 (contains INTERNAL and CONFIDENTIAL evidence)
    c1 = next(c for c in proj.controls if c.id == CA_1_ID)
    assert len(c1.evidence) == 1
    assert c1.evidence[0].id == EV_INTERNAL_ID
    assert c1.evidence[0].snippet == "Internal password guidelines"
    assert c1.hidden_evidence_present is True

    # 3. Control 2 (contains RESTRICTED evidence only)
    c2 = next(c for c in proj.controls if c.id == CA_2_ID)
    assert len(c2.evidence) == 0
    assert c2.hidden_evidence_present is True

    # 4. Verify no hidden evidence metadata, IDs, or snippets leak anywhere in projection
    proj_str = str(proj.model_dump())
    assert str(EV_CONFIDENTIAL_ID) not in proj_str
    assert str(EV_RESTRICTED_ID) not in proj_str
    assert "Confidential network topology" not in proj_str
    assert "Restricted executive board" not in proj_str


async def test_manager_projection_sees_confidential_but_redacts_restricted(
    service: ComplianceAssessmentService,
) -> None:
    proj = await service.get_assessment_projection(
        principal=MANAGER_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )

    assert proj.overall_score == Decimal("75.00")
    assert proj.any_hidden_evidence is True

    # Control 1 has INTERNAL and CONFIDENTIAL -> both visible to Manager
    c1 = next(c for c in proj.controls if c.id == CA_1_ID)
    assert len(c1.evidence) == 2
    assert {e.id for e in c1.evidence} == {EV_INTERNAL_ID, EV_CONFIDENTIAL_ID}
    assert c1.hidden_evidence_present is False

    # Control 2 has RESTRICTED -> hidden from Manager
    c2 = next(c for c in proj.controls if c.id == CA_2_ID)
    assert len(c2.evidence) == 0
    assert c2.hidden_evidence_present is True

    # Restricted metadata does not leak
    proj_str = str(proj.model_dump())
    assert str(EV_RESTRICTED_ID) not in proj_str
    assert "Restricted executive board" not in proj_str


async def test_admin_projection_sees_restricted_evidence(
    service: ComplianceAssessmentService,
) -> None:
    # Admin has RISK_READ and RESTRICTED clearance
    proj = await service.get_assessment_projection(
        principal=ADMIN_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )

    assert proj.overall_score == Decimal("75.00")
    assert proj.any_hidden_evidence is False

    c1 = next(c for c in proj.controls if c.id == CA_1_ID)
    assert len(c1.evidence) == 2
    assert c1.hidden_evidence_present is False

    c2 = next(c for c in proj.controls if c.id == CA_2_ID)
    assert len(c2.evidence) == 1
    assert c2.evidence[0].id == EV_RESTRICTED_ID
    assert c2.hidden_evidence_present is False


async def test_actor_dependent_projection_preserves_identical_scores(
    service: ComplianceAssessmentService,
) -> None:
    """Proves that Analyst, Manager, and Admin projections produce different visible evidence
    but EXACTLY identical mathematical scores.
    """
    analyst_proj = await service.get_assessment_projection(
        principal=ANALYST_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )
    manager_proj = await service.get_assessment_projection(
        principal=MANAGER_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )
    admin_proj = await service.get_assessment_projection(
        principal=ADMIN_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )

    # Identical score outputs across all callers
    assert (
        analyst_proj.overall_score
        == manager_proj.overall_score
        == admin_proj.overall_score
    )
    assert (
        analyst_proj.risk_classification
        == manager_proj.risk_classification
        == admin_proj.risk_classification
    )
    assert (
        analyst_proj.scoring_version
        == manager_proj.scoring_version
        == admin_proj.scoring_version
    )

    # Visible evidence counts differ according to clearance
    analyst_ev_count = sum(len(c.evidence) for c in analyst_proj.controls)
    manager_ev_count = sum(len(c.evidence) for c in manager_proj.controls)
    admin_ev_count = sum(len(c.evidence) for c in admin_proj.controls)

    assert analyst_ev_count == 1
    assert manager_ev_count == 2
    assert admin_ev_count == 3


async def test_projection_foreign_tenant_raises_not_found(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.get_assessment.return_value = None
    with pytest.raises(NotFoundError):
        await service.get_assessment_projection(
            principal=ANALYST_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
        )
