"""Unit tests for compliance FastAPI API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api.deps import get_db_session, get_principal
from app.api.v1.compliance import router as compliance_router
from app.core.errors import (
    register_exception_handlers,
)
from app.models.compliance import (
    AssessmentScoreSnapshotProjection,
    ComplianceAssessmentProjection,
    ComplianceAssessmentResponse,
    ControlAssessmentProjection,
    ScoreOverrideResponse,
    VisibleEvidenceReference,
)
from app.models.enums import (
    AssessmentStatus,
    ConfidentialityLevel,
    ControlStatus,
    RiskClassification,
    Role,
)
from app.models.principal import Principal
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

ORG_ID = uuid4()
USER_ANALYST = uuid4()
USER_MANAGER = uuid4()
USER_ADMIN = uuid4()
FRAMEWORK_ID = uuid4()
ASSESSMENT_ID = uuid4()
CONTROL_ASSESSMENT_ID = uuid4()
SNAPSHOT_ID = uuid4()
OVERRIDE_ID = uuid4()
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


@pytest.fixture
def mock_compliance_service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_candidate_service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def app(
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
) -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(compliance_router, prefix="/api/v1")
    return application


@pytest.fixture
def client(
    app: FastAPI,
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    from app.api.v1 import compliance

    monkeypatch.setattr(compliance, "_service", lambda session: mock_compliance_service)
    monkeypatch.setattr(
        compliance, "_candidate_service", lambda session, container: mock_candidate_service
    )
    return TestClient(app)


def _set_principal(app: FastAPI, principal: Principal) -> None:
    async def override_get_principal() -> Principal:
        return principal

    async def override_get_db_session() -> AsyncMock:
        return AsyncMock()

    app.dependency_overrides[get_principal] = override_get_principal
    app.dependency_overrides[get_db_session] = override_get_db_session


# -----------------------------------------------------------------------------
# Assessment Creation & Listing API Tests
# -----------------------------------------------------------------------------


def test_analyst_can_create_assessment(
    client: TestClient,
    app: FastAPI,
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
) -> None:
    _set_principal(app, ANALYST_PRINCIPAL)
    now = datetime.now(UTC)
    mock_compliance_service.create_assessment.return_value = ComplianceAssessmentResponse(
        id=ASSESSMENT_ID,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="SOC 2 Assessment",
        status=AssessmentStatus.DRAFT,
        overall_score=None,
        risk_classification=RiskClassification.NOT_SCORED,
        scoring_version=None,
        created_by=USER_ANALYST,
        created_at=now,
        updated_at=now,
    )

    resp = client.post(
        "/api/v1/compliance/assessments",
        json={"framework_id": str(FRAMEWORK_ID), "title": "SOC 2 Assessment"},
    )
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["id"] == str(ASSESSMENT_ID)
    assert data["title"] == "SOC 2 Assessment"


def test_admin_cannot_create_assessment(
    client: TestClient,
    app: FastAPI,
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
) -> None:
    _set_principal(app, ADMIN_PRINCIPAL)
    resp = client.post(
        "/api/v1/compliance/assessments",
        json={"framework_id": str(FRAMEWORK_ID), "title": "Admin Attempt"},
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_get_assessment_clearance_projection(
    client: TestClient,
    app: FastAPI,
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
) -> None:
    _set_principal(app, ANALYST_PRINCIPAL)
    now = datetime.now(UTC)
    mock_compliance_service.get_assessment_projection.return_value = ComplianceAssessmentProjection(
        id=ASSESSMENT_ID,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Projected Assessment",
        status=AssessmentStatus.IN_PROGRESS,
        overall_score=Decimal("85.00"),
        risk_classification=RiskClassification.LOW,
        scoring_version="v1.0",
        created_by=USER_ANALYST,
        created_at=now,
        updated_at=now,
        effective_overall_score=Decimal("85.00"),
        effective_risk_classification=RiskClassification.LOW,
        controls=[
            ControlAssessmentProjection(
                id=CONTROL_ASSESSMENT_ID,
                organization_id=ORG_ID,
                assessment_id=ASSESSMENT_ID,
                control_id=uuid4(),
                status=ControlStatus.SATISFIED,
                effective_weight=Decimal("2.0"),
                rationale="Verified",
                created_at=now,
                updated_at=now,
                evidence=[
                    VisibleEvidenceReference(
                        id=uuid4(),
                        control_assessment_id=CONTROL_ASSESSMENT_ID,
                        document_id=DOC_ID,
                        chunk_id=None,
                        confidentiality_level=ConfidentialityLevel.INTERNAL,
                        snippet="Internal policy evidence snippet",
                        created_by=USER_ANALYST,
                        created_at=now,
                    )
                ],
                hidden_evidence_present=True,
            )
        ],
        any_hidden_evidence=True,
    )

    resp = client.get(f"/api/v1/compliance/assessments/{ASSESSMENT_ID}")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["id"] == str(ASSESSMENT_ID)
    assert data["any_hidden_evidence"] is True
    control = data["controls"][0]
    assert control["hidden_evidence_present"] is True
    assert len(control["evidence"]) == 1
    assert "Internal policy evidence snippet" in control["evidence"][0]["snippet"]


# -----------------------------------------------------------------------------
# Finalization & Override API Tests
# -----------------------------------------------------------------------------


def test_manager_can_finalize_assessment(
    client: TestClient,
    app: FastAPI,
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
) -> None:
    _set_principal(app, MANAGER_PRINCIPAL)
    now = datetime.now(UTC)
    mock_compliance_service.finalize_assessment.return_value = ComplianceAssessmentResponse(
        id=ASSESSMENT_ID,
        organization_id=ORG_ID,
        framework_id=FRAMEWORK_ID,
        title="Finalized Assessment",
        status=AssessmentStatus.COMPLETED,
        overall_score=Decimal("92.00"),
        risk_classification=RiskClassification.LOW,
        scoring_version="v1.0",
        created_by=USER_ANALYST,
        created_at=now,
        updated_at=now,
        effective_overall_score=Decimal("92.00"),
        effective_risk_classification=RiskClassification.LOW,
    )

    resp = client.post(f"/api/v1/compliance/assessments/{ASSESSMENT_ID}/finalize")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["status"] == "completed"


def test_analyst_cannot_finalize_assessment(
    client: TestClient,
    app: FastAPI,
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
) -> None:
    _set_principal(app, ANALYST_PRINCIPAL)
    resp = client.post(f"/api/v1/compliance/assessments/{ASSESSMENT_ID}/finalize")
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_manager_can_create_score_override(
    client: TestClient,
    app: FastAPI,
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
) -> None:
    _set_principal(app, MANAGER_PRINCIPAL)
    mock_compliance_service.create_score_override.return_value = ScoreOverrideResponse(
        id=OVERRIDE_ID,
        organization_id=ORG_ID,
        assessment_id=ASSESSMENT_ID,
        snapshot_id=SNAPSHOT_ID,
        source_revision_number=1,
        original_overall_score=Decimal("65.00"),
        original_risk_classification=RiskClassification.MEDIUM,
        override_overall_score=Decimal("88.00"),
        override_risk_classification=RiskClassification.LOW,
        justification="Executive risk acceptance",
        overridden_by=USER_MANAGER,
        overridden_at=datetime.now(UTC),
    )

    resp = client.post(
        f"/api/v1/compliance/assessments/{ASSESSMENT_ID}/override",
        json={
            "override_overall_score": 88.0,
            "justification": "Executive risk acceptance",
        },
    )
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["id"] == str(OVERRIDE_ID)
    assert Decimal(str(data["override_overall_score"])) == Decimal("88.00")
    assert data["override_risk_classification"] == "low"


def test_analyst_cannot_create_score_override(
    client: TestClient,
    app: FastAPI,
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
) -> None:
    _set_principal(app, ANALYST_PRINCIPAL)
    resp = client.post(
        f"/api/v1/compliance/assessments/{ASSESSMENT_ID}/override",
        json={
            "override_overall_score": 88.0,
            "justification": "Analyst override attempt",
        },
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_list_score_overrides(
    client: TestClient,
    app: FastAPI,
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
) -> None:
    _set_principal(app, ADMIN_PRINCIPAL)
    mock_compliance_service.list_score_overrides.return_value = [
        ScoreOverrideResponse(
            id=OVERRIDE_ID,
            organization_id=ORG_ID,
            assessment_id=ASSESSMENT_ID,
            snapshot_id=SNAPSHOT_ID,
            source_revision_number=1,
            original_overall_score=Decimal("65.00"),
            original_risk_classification=RiskClassification.MEDIUM,
            override_overall_score=Decimal("88.00"),
            override_risk_classification=RiskClassification.LOW,
            justification="Executive risk acceptance",
            overridden_by=USER_MANAGER,
            overridden_at=datetime.now(UTC),
        )
    ]

    resp = client.get(f"/api/v1/compliance/assessments/{ASSESSMENT_ID}/overrides")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(OVERRIDE_ID)


def test_list_snapshots_returns_clearance_safe_projection_without_input_snapshot(
    client: TestClient,
    app: FastAPI,
    mock_compliance_service: AsyncMock,
    mock_candidate_service: AsyncMock,
) -> None:
    _set_principal(app, ANALYST_PRINCIPAL)
    secret_evidence_id = uuid4()
    mock_compliance_service.list_snapshots.return_value = [
        AssessmentScoreSnapshotProjection(
            id=SNAPSHOT_ID,
            organization_id=ORG_ID,
            assessment_id=ASSESSMENT_ID,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2017",
            raw_scores={"raw_final_score": "85.00"},
            overall_score=Decimal("85.00"),
            risk_classification=RiskClassification.LOW,
            computed_by=USER_ANALYST,
            computed_at=datetime.now(UTC),
        )
    ]

    resp = client.get(f"/api/v1/compliance/assessments/{ASSESSMENT_ID}/snapshots")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data) == 1
    snapshot_data = data[0]
    assert snapshot_data["id"] == str(SNAPSHOT_ID)
    assert snapshot_data["revision_number"] == 1
    assert Decimal(str(snapshot_data["overall_score"])) == Decimal("85.00")
    assert snapshot_data["risk_classification"] == "low"
    assert "input_snapshot" not in snapshot_data
    assert str(secret_evidence_id) not in resp.text
