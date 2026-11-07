"""Unit tests for compliance score override service and domain logic."""

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
    ScoreOverrideCreateRequest,
    ScoreOverrideResponse,
)
from app.models.enums import (
    AssessmentStatus,
    RiskClassification,
    Role,
)
from app.models.principal import Principal
from app.ports.compliance_repository import ComplianceRepository
from app.services.compliance import ComplianceAssessmentService
from app.services.compliance_scoring import RiskScoringEngine
from pydantic import ValidationError as PydanticValidationError

ORG_ID = uuid4()
USER_ANALYST = uuid4()
USER_MANAGER = uuid4()
USER_ADMIN = uuid4()
FRAMEWORK_ID = uuid4()
ASSESSMENT_ID = uuid4()
SNAPSHOT_ID = uuid4()

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
    overall_score: Decimal | None = Decimal("70.00"),
    risk_classification: RiskClassification = RiskClassification.MEDIUM,
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


def _make_snapshot(
    *,
    overall_score: Decimal | None = Decimal("70.00"),
    risk_classification: RiskClassification = RiskClassification.MEDIUM,
    revision_number: int = 1,
) -> AssessmentScoreSnapshotResponse:
    return AssessmentScoreSnapshotResponse(
        id=SNAPSHOT_ID,
        organization_id=ORG_ID,
        assessment_id=ASSESSMENT_ID,
        revision_number=revision_number,
        scoring_version="v1.0",
        framework_version="2017",
        input_snapshot={"scoring_input": {}},
        raw_scores={"raw": {}},
        overall_score=overall_score,
        risk_classification=risk_classification,
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
    repo.create_score_override.side_effect = lambda **kwargs: ScoreOverrideResponse(
        id=uuid4(),
        organization_id=kwargs["organization_id"],
        assessment_id=kwargs["assessment_id"],
        snapshot_id=kwargs["snapshot_id"],
        source_revision_number=kwargs["source_revision_number"],
        original_overall_score=kwargs["original_overall_score"],
        original_risk_classification=kwargs["original_risk_classification"],
        override_overall_score=kwargs["override_overall_score"],
        override_risk_classification=kwargs["override_risk_classification"],
        justification=kwargs["justification"],
        overridden_by=kwargs["overridden_by"],
        overridden_at=datetime.now(UTC),
    )
    return repo


@pytest.fixture
def service(mock_repo: AsyncMock) -> ComplianceAssessmentService:
    return ComplianceAssessmentService(repository=mock_repo)


# -----------------------------------------------------------------------------
# ScoreOverride Validation Tests
# -----------------------------------------------------------------------------


def test_override_score_half_up_quantization() -> None:
    req = ScoreOverrideCreateRequest(
        override_overall_score=Decimal("85.126"),
        justification="Manual risk adjustment",
    )
    assert req.override_overall_score == Decimal("85.13")


def test_override_score_out_of_bounds_raises() -> None:
    with pytest.raises(PydanticValidationError):
        ScoreOverrideCreateRequest(
            override_overall_score=Decimal("100.01"),
            justification="Out of bounds",
        )

    with pytest.raises(PydanticValidationError):
        ScoreOverrideCreateRequest(
            override_overall_score=Decimal("-0.01"),
            justification="Negative score",
        )


def test_blank_justification_raises() -> None:
    with pytest.raises(PydanticValidationError):
        ScoreOverrideCreateRequest(
            override_overall_score=Decimal("80.00"),
            justification="   ",
        )


# -----------------------------------------------------------------------------
# Deterministic Classification Derivation Tests
# -----------------------------------------------------------------------------


def test_risk_scoring_engine_classify_score() -> None:
    # LOW: residual risk < 25.00 -> score > 75.00
    assert RiskScoringEngine.classify_score(Decimal("100.00")) == RiskClassification.LOW
    assert RiskScoringEngine.classify_score(Decimal("75.01")) == RiskClassification.LOW

    # MEDIUM: 25.00 <= residual risk < 50.00 -> 50.00 < score <= 75.00
    assert RiskScoringEngine.classify_score(Decimal("75.00")) == RiskClassification.MEDIUM
    assert RiskScoringEngine.classify_score(Decimal("50.01")) == RiskClassification.MEDIUM

    # HIGH: 50.00 <= residual risk < 75.00 -> 25.00 < score <= 50.00
    assert RiskScoringEngine.classify_score(Decimal("50.00")) == RiskClassification.HIGH
    assert RiskScoringEngine.classify_score(Decimal("25.01")) == RiskClassification.HIGH

    # CRITICAL: residual risk >= 75.00 -> score <= 25.00
    assert RiskScoringEngine.classify_score(Decimal("25.00")) == RiskClassification.CRITICAL
    assert RiskScoringEngine.classify_score(Decimal("0.00")) == RiskClassification.CRITICAL


# -----------------------------------------------------------------------------
# ScoreOverride Authorization Tests
# -----------------------------------------------------------------------------


async def test_manager_can_override_score(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    req = ScoreOverrideCreateRequest(
        override_overall_score=Decimal("90.00"),
        justification="Compensating controls reviewed by SecOps manager",
    )
    result = await service.create_score_override(
        principal=MANAGER_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
        request=req,
    )
    assert result.override_overall_score == Decimal("90.00")
    assert result.override_risk_classification == RiskClassification.LOW
    assert result.original_overall_score == Decimal("70.00")
    assert result.original_risk_classification == RiskClassification.MEDIUM
    assert result.overridden_by == USER_MANAGER
    assert result.source_revision_number == 1
    mock_repo.create_score_override.assert_called_once()


async def test_analyst_cannot_override_score(
    service: ComplianceAssessmentService,
) -> None:
    req = ScoreOverrideCreateRequest(
        override_overall_score=Decimal("90.00"),
        justification="Analyst attempting override",
    )
    with pytest.raises(AuthorizationError):
        await service.create_score_override(
            principal=ANALYST_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
            request=req,
        )


async def test_admin_cannot_override_score(
    service: ComplianceAssessmentService,
) -> None:
    req = ScoreOverrideCreateRequest(
        override_overall_score=Decimal("90.00"),
        justification="Admin attempting override",
    )
    with pytest.raises(AuthorizationError):
        await service.create_score_override(
            principal=ADMIN_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
            request=req,
        )


# -----------------------------------------------------------------------------
# ScoreOverride State Checks
# -----------------------------------------------------------------------------


async def test_override_without_snapshot_raises_conflict(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.get_latest_snapshot.return_value = None
    req = ScoreOverrideCreateRequest(
        override_overall_score=Decimal("80.00"),
        justification="Override before compute",
    )
    with pytest.raises(ConflictError) as exc_info:
        await service.create_score_override(
            principal=MANAGER_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
            request=req,
        )
    assert "Cannot override score before at least one score computation snapshot" in str(
        exc_info.value
    )


async def test_override_not_scored_assessment_raises_conflict(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.lock_assessment.return_value = _make_assessment(
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
    )
    mock_repo.get_latest_snapshot.return_value = _make_snapshot(
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
    )
    req = ScoreOverrideCreateRequest(
        override_overall_score=Decimal("80.00"),
        justification="Override NOT_SCORED framework",
    )
    with pytest.raises(ConflictError) as exc_info:
        await service.create_score_override(
            principal=MANAGER_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
            request=req,
        )
    assert "zero applicable controls" in str(exc_info.value)


async def test_override_archived_assessment_raises_conflict(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.lock_assessment.return_value = _make_assessment(
        status=AssessmentStatus.ARCHIVED
    )
    req = ScoreOverrideCreateRequest(
        override_overall_score=Decimal("80.00"),
        justification="Override archived",
    )
    with pytest.raises(ConflictError) as exc_info:
        await service.create_score_override(
            principal=MANAGER_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
            request=req,
        )
    assert "Archived compliance assessments cannot be overridden" in str(exc_info.value)


async def test_override_nonexistent_assessment_raises_not_found(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.lock_assessment.return_value = None
    req = ScoreOverrideCreateRequest(
        override_overall_score=Decimal("80.00"),
        justification="Override missing",
    )
    with pytest.raises(NotFoundError):
        await service.create_score_override(
            principal=MANAGER_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
            request=req,
        )


async def test_list_score_overrides_tenant_enforcement(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    mock_repo.list_score_overrides.return_value = [
        ScoreOverrideResponse(
            id=uuid4(),
            organization_id=ORG_ID,
            assessment_id=ASSESSMENT_ID,
            snapshot_id=SNAPSHOT_ID,
            source_revision_number=1,
            original_overall_score=Decimal("70.00"),
            original_risk_classification=RiskClassification.MEDIUM,
            override_overall_score=Decimal("85.00"),
            override_risk_classification=RiskClassification.LOW,
            justification="Review approved",
            overridden_by=USER_MANAGER,
            overridden_at=datetime.now(UTC),
        )
    ]
    overrides = await service.list_score_overrides(
        principal=ANALYST_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )
    assert len(overrides) == 1
    assert overrides[0].override_overall_score == Decimal("85.00")


async def test_override_becomes_inactive_when_mutation_invalidates_current_score(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    """When an assessment is invalidated (scoring_version=None), active override is None."""
    mock_repo.get_assessment.return_value = _make_assessment(
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
    ).model_copy(update={"scoring_version": None})
    mock_repo.get_latest_score_override.return_value = ScoreOverrideResponse(
        id=uuid4(),
        organization_id=ORG_ID,
        assessment_id=ASSESSMENT_ID,
        snapshot_id=SNAPSHOT_ID,
        source_revision_number=1,
        original_overall_score=Decimal("70.00"),
        original_risk_classification=RiskClassification.MEDIUM,
        override_overall_score=Decimal("85.00"),
        override_risk_classification=RiskClassification.LOW,
        justification="Review approved",
        overridden_by=USER_MANAGER,
        overridden_at=datetime.now(UTC),
    )

    assessment = await service.get_assessment(
        principal=ANALYST_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )
    assert assessment.latest_override is None
    assert assessment.effective_overall_score is None
    assert assessment.effective_risk_classification == RiskClassification.NOT_SCORED


async def test_override_becomes_inactive_when_recomputed_to_rev2(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    """When assessment is recomputed to revision 2, revision 1 override is no longer active."""
    rev2_snapshot_id = uuid4()
    mock_repo.get_assessment.return_value = _make_assessment(
        overall_score=Decimal("90.00"),
        risk_classification=RiskClassification.LOW,
    )
    mock_repo.get_latest_snapshot.return_value = _make_snapshot(
        overall_score=Decimal("90.00"),
        risk_classification=RiskClassification.LOW,
        revision_number=2,
    ).model_copy(update={"id": rev2_snapshot_id})
    # Latest override in DB is still pointing to revision 1
    mock_repo.get_latest_score_override.return_value = ScoreOverrideResponse(
        id=uuid4(),
        organization_id=ORG_ID,
        assessment_id=ASSESSMENT_ID,
        snapshot_id=SNAPSHOT_ID,
        source_revision_number=1,  # Points to rev 1
        original_overall_score=Decimal("70.00"),
        original_risk_classification=RiskClassification.MEDIUM,
        override_overall_score=Decimal("85.00"),
        override_risk_classification=RiskClassification.LOW,
        justification="Rev 1 override",
        overridden_by=USER_MANAGER,
        overridden_at=datetime.now(UTC),
    )

    assessment = await service.get_assessment(
        principal=ANALYST_PRINCIPAL,
        assessment_id=ASSESSMENT_ID,
    )
    assert assessment.latest_override is None
    assert assessment.effective_overall_score == Decimal("90.00")
    assert assessment.effective_risk_classification == RiskClassification.LOW


async def test_override_fails_when_assessment_state_is_stale_or_invalidated(
    service: ComplianceAssessmentService,
    mock_repo: AsyncMock,
) -> None:
    """Manager cannot create override on an assessment whose computation is stale/invalidated."""
    # Assessment was mutated after compute, so scoring_version is None
    mock_repo.lock_assessment.return_value = _make_assessment(
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
    ).model_copy(update={"scoring_version": None})
    mock_repo.get_latest_snapshot.return_value = _make_snapshot(
        overall_score=Decimal("70.00"),
        risk_classification=RiskClassification.MEDIUM,
    )

    req = ScoreOverrideCreateRequest(
        override_overall_score=Decimal("85.00"),
        justification="Attempt override on stale assessment",
    )
    with pytest.raises(ConflictError) as exc_info:
        await service.create_score_override(
            principal=MANAGER_PRINCIPAL,
            assessment_id=ASSESSMENT_ID,
            request=req,
        )
    assert "modified since last computation" in str(exc_info.value)
