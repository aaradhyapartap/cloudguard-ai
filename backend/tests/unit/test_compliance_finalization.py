"""Unit tests for compliance assessment finalization and completed-state immutability."""

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
)
from app.models.compliance import (
    AssessmentScoreSnapshotResponse,
    ComplianceAssessmentResponse,
    ControlAssessmentResponse,
    ControlAssessmentUpdateRequest,
    EvidenceReferenceCreateRequest,
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
SNAPSHOT_ID = uuid4()
CONTROL_ASSESSMENT_ID = uuid4()
DOC_ID = uuid4()

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


def _make_assessment(
    *,
    status: AssessmentStatus = AssessmentStatus.IN_PROGRESS,
    overall_score: Decimal | None = Decimal("80.00"),
    risk_classification: RiskClassification = RiskClassification.LOW,
) -> ComplianceAssessmentResponse:
    now = datetime.now(UTC)
    return ComplianceAssessmentResponse(
        id=ASSESSMENT_ID,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="SOC 2 Assessment",
        status=status,
        overall_score=overall_score,
        risk_classification=risk_classification,
        scoring_version="v1.0",
        created_by=USER_ANALYST,
        created_at=now,
        updated_at=now,
    )


def _make_snapshot() -> AssessmentScoreSnapshotResponse:
    return AssessmentScoreSnapshotResponse(
        id=SNAPSHOT_ID,
        organization_id=ORG_ID,
        assessment_id=ASSESSMENT_ID,
        revision_number=1,
        scoring_version="v1.0",
        framework_version="2017",
        input_snapshot={},
        raw_scores={},
        overall_score=Decimal("80.00"),
        risk_classification=RiskClassification.LOW,
        computed_by=USER_ANALYST,
        computed_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock(spec=ComplianceRepository)
    repo.lock_assessment.return_value = _make_assessment()
    repo.get_assessment.return_value = _make_assessment()
    repo.get_latest_snapshot.return_value = _make_snapshot()
    repo.get_latest_score_override.return_value = None
    repo.finalize_assessment.return_value = _make_assessment(
        status=AssessmentStatus.COMPLETED
    )
    repo.get_control_assessment.return_value = ControlAssessmentResponse(
        id=CONTROL_ASSESSMENT_ID,
        organization_id=ORG_ID,
        assessment_id=ASSESSMENT_ID,
        control_id=uuid4(),
        status=ControlStatus.SATISFIED,
        effective_weight=Decimal("2.0"),
        rationale=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo.get_document_for_evidence.return_value = (DOC_ID, ConfidentialityLevel.INTERNAL)
    return repo


@pytest.fixture
def service(mock_repo: AsyncMock) -> ComplianceAssessmentService:
    return ComplianceAssessmentService(repository=mock_repo)


# -----------------------------------------------------------------------------
# Finalization Authorization Tests
# -----------------------------------------------------------------------------


async def test_manager_can_finalize_assessment(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    res = await service.finalize_assessment(
        principal=MANAGER_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )
    assert res.status == AssessmentStatus.COMPLETED
    assert res.overall_score == Decimal("80.00")
    assert res.effective_overall_score == Decimal("80.00")
    mock_repo.finalize_assessment.assert_called_once_with(
        organization_id=ORG_ID,
        assessment_id=ASSESSMENT_ID,
    )


async def test_analyst_cannot_finalize_assessment(
    service: ComplianceAssessmentService,
) -> None:
    with pytest.raises(AuthorizationError):
        await service.finalize_assessment(
            principal=ANALYST_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
        )


async def test_admin_cannot_finalize_assessment(
    service: ComplianceAssessmentService,
) -> None:
    with pytest.raises(AuthorizationError):
        await service.finalize_assessment(
            principal=ADMIN_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
        )


# -----------------------------------------------------------------------------
# Finalization State Checks
# -----------------------------------------------------------------------------


async def test_finalize_without_snapshot_raises_conflict(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.get_latest_snapshot.return_value = None
    with pytest.raises(ConflictError) as exc_info:
        await service.finalize_assessment(
            principal=MANAGER_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
        )
    assert "at least one score computation snapshot" in str(exc_info.value)


async def test_cannot_finalize_already_completed_assessment(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.lock_assessment.return_value = _make_assessment(
        status=AssessmentStatus.COMPLETED
    )
    with pytest.raises(ConflictError) as exc_info:
        await service.finalize_assessment(
            principal=MANAGER_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
        )
    assert "already completed" in str(exc_info.value)


async def test_finalize_nonexistent_assessment_raises_not_found(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.lock_assessment.return_value = None
    with pytest.raises(NotFoundError):
        await service.finalize_assessment(
            principal=MANAGER_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
        )


# -----------------------------------------------------------------------------
# Completed Assessment Immutability
# -----------------------------------------------------------------------------


async def test_completed_assessment_rejects_control_mutation(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.lock_assessment.return_value = _make_assessment(
        status=AssessmentStatus.COMPLETED
    )
    with pytest.raises(ConflictError) as exc_info:
        await service.update_control_assessment(
            principal=ANALYST_PRINCIPAL,
            control_assessment_id=CONTROL_ASSESSMENT_ID,
            request=ControlAssessmentUpdateRequest(status=ControlStatus.DEFICIENT),
        )
    assert "Completed or archived compliance assessments cannot be modified" in str(
        exc_info.value
    )


async def test_completed_assessment_rejects_evidence_admission(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.lock_assessment.return_value = _make_assessment(
        status=AssessmentStatus.COMPLETED
    )
    with pytest.raises(ConflictError) as exc_info:
        await service.admit_evidence(
            principal=ANALYST_PRINCIPAL,
            control_assessment_id=CONTROL_ASSESSMENT_ID,
            request=EvidenceReferenceCreateRequest(document_id=DOC_ID),
        )
    assert "completed or archived" in str(exc_info.value)


async def test_completed_assessment_rejects_recomputation(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.lock_assessment.return_value = _make_assessment(
        status=AssessmentStatus.COMPLETED
    )
    with pytest.raises(ConflictError) as exc_info:
        await service.compute_score(
            principal=ANALYST_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
        )
    assert "Completed or archived compliance assessments cannot be recomputed" in str(
        exc_info.value
    )


async def test_finalization_fails_when_assessment_state_is_stale_or_invalidated(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    """Finalization fails when assessment state has been invalidated by mutation."""
    mock_repo.lock_assessment.return_value = _make_assessment(
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
    ).model_copy(update={"scoring_version": None})
    mock_repo.get_latest_snapshot.return_value = _make_snapshot()

    with pytest.raises(ConflictError) as exc_info:
        await service.finalize_assessment(
            principal=MANAGER_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
        )
    assert "modified since the last computation" in str(exc_info.value)


async def test_finalization_succeeds_for_current_zero_applicable_not_scored_assessment(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    """Finalization succeeds for a computed zero-applicable assessment if computation is current."""
    not_scored_assessment = _make_assessment(
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
    )
    mock_repo.lock_assessment.return_value = not_scored_assessment
    mock_repo.get_latest_snapshot.return_value = AssessmentScoreSnapshotResponse(
        id=SNAPSHOT_ID,
        organization_id=ORG_ID,
        assessment_id=ASSESSMENT_ID,
        revision_number=1,
        scoring_version="v1.0",
        framework_version="2017",
        input_snapshot={},
        raw_scores={},
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        computed_by=USER_ANALYST,
        computed_at=datetime.now(UTC),
    )
    mock_repo.finalize_assessment.return_value = not_scored_assessment.model_copy(
        update={"status": AssessmentStatus.COMPLETED}
    )

    finalized = await service.finalize_assessment(
        principal=MANAGER_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )
    assert finalized.status == AssessmentStatus.COMPLETED
    assert finalized.overall_score is None
    assert finalized.risk_classification == RiskClassification.NOT_SCORED
