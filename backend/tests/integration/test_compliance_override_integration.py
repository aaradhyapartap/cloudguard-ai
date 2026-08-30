"""Integration tests for ScoreOverride persistence, immutability trigger, and tenant RLS."""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from app.adapters.local.compliance_repository import SQLAlchemyComplianceRepository
from app.core.errors import ConflictError
from app.models.compliance import (
    ComplianceAssessmentCreateRequest,
    ControlAssessmentUpdateRequest,
    EvidenceReferenceCreateRequest,
    ScoreOverrideCreateRequest,
)
from app.models.enums import (
    AssessmentStatus,
    ConfidentialityLevel,
    ControlStatus,
    DocumentType,
    ProcessingStatus,
    RiskClassification,
    Role,
)
from app.models.principal import Principal
from app.repositories.database import (
    tenant_session,
    untenanted_session,
)
from app.repositories.tables import (
    AssessmentScoreSnapshot,
    ComplianceAssessment,
    ComplianceControl,
    ComplianceFramework,
    Document,
    Organization,
    ScoreOverride,
    User,
)
from app.services.compliance import ComplianceAssessmentService
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

pytestmark = pytest.mark.integration

ORG_A = uuid4()
ORG_B = uuid4()
USER_A = uuid4()
USER_B = uuid4()
FRAMEWORK_ID = uuid4()
CONTROL_1_ID = uuid4()


@pytest.fixture(scope="module", autouse=True)
async def seed_integration_data() -> None:
    """Seed prerequisite tenant, framework, and control rows."""
    if not os.environ.get("RUN_DB_TESTS"):
        pytest.skip("Database tests skipped unless RUN_DB_TESTS=1 is set.")

    # Untenanted: Seed organizations, global framework, and controls
    async with untenanted_session() as session:
        session.add(
            Organization(
                id=ORG_A,
                name="Acme Org A",
                slug=f"acme-a-{ORG_A.hex[:8]}",
            )
        )
        session.add(
            Organization(
                id=ORG_B,
                name="Beta Org B",
                slug=f"beta-b-{ORG_B.hex[:8]}",
            )
        )
        await session.commit()

    async with untenanted_session() as session:
        session.add(
            ComplianceFramework(
                id=FRAMEWORK_ID,
                code=f"SOC2_{FRAMEWORK_ID.hex[:8]}",
                name="SOC 2 Type II",
                version="2017",
                description="SOC 2 Trust Services Criteria",
            )
        )
        await session.commit()

    async with untenanted_session() as session:
        session.add(
            ComplianceControl(
                id=CONTROL_1_ID,
                framework_id=FRAMEWORK_ID,
                control_code="CC6.1",
                title="Logical Access Controls",
                description="Logical access is restricted to authorized users.",
                category="Security",
                default_weight=Decimal("3.0"),
            )
        )
        await session.commit()

    # Seed users inside tenant sessions
    async with tenant_session(ORG_A) as session:
        session.add(
            User(
                id=USER_A,
                organization_id=ORG_A,
                email=f"manager_a_{USER_A.hex[:6]}@acme.test",
                role=Role.MANAGER,
            )
        )
        await session.commit()

    async with tenant_session(ORG_B) as session:
        session.add(
            User(
                id=USER_B,
                organization_id=ORG_B,
                email=f"manager_b_{USER_B.hex[:6]}@beta.test",
                role=Role.MANAGER,
            )
        )
        await session.commit()


async def test_score_override_rls_tenant_isolation() -> None:
    """ScoreOverride rows are completely isolated between tenants under RLS."""
    assessment_a_id = uuid4()
    assessment_b_id = uuid4()
    snapshot_a_id = uuid4()
    snapshot_b_id = uuid4()
    override_a_id = uuid4()
    override_b_id = uuid4()

    # Create assessment and snapshot for Org A
    async with tenant_session(ORG_A) as session:
        session.add(
            ComplianceAssessment(
                id=assessment_a_id,
                organization_id=ORG_A,
                framework_id=FRAMEWORK_ID,
                title="Org A Assessment",
                status=AssessmentStatus.IN_PROGRESS,
                overall_score=Decimal("60.00"),
                risk_classification=RiskClassification.MEDIUM,
                created_by=USER_A,
            )
        )
        await session.flush()
        session.add(
            AssessmentScoreSnapshot(
                id=snapshot_a_id,
                organization_id=ORG_A,
                assessment_id=assessment_a_id,
                revision_number=1,
                scoring_version="v1.0",
                framework_version="2017",
                input_snapshot={"scoring_input": {}},
                raw_scores={"raw": {}},
                overall_score=Decimal("60.00"),
                risk_classification=RiskClassification.MEDIUM,
                computed_by=USER_A,
            )
        )
        await session.flush()
        session.add(
            ScoreOverride(
                id=override_a_id,
                organization_id=ORG_A,
                assessment_id=assessment_a_id,
                snapshot_id=snapshot_a_id,
                source_revision_number=1,
                original_overall_score=Decimal("60.00"),
                original_risk_classification=RiskClassification.MEDIUM,
                override_overall_score=Decimal("85.00"),
                override_risk_classification=RiskClassification.LOW,
                justification="Org A Manager approval",
                overridden_by=USER_A,
            )
        )
        await session.commit()

    # Create assessment and snapshot for Org B
    async with tenant_session(ORG_B) as session:
        session.add(
            ComplianceAssessment(
                id=assessment_b_id,
                organization_id=ORG_B,
                framework_id=FRAMEWORK_ID,
                title="Org B Assessment",
                status=AssessmentStatus.IN_PROGRESS,
                overall_score=Decimal("40.00"),
                risk_classification=RiskClassification.HIGH,
                created_by=USER_B,
            )
        )
        await session.flush()
        session.add(
            AssessmentScoreSnapshot(
                id=snapshot_b_id,
                organization_id=ORG_B,
                assessment_id=assessment_b_id,
                revision_number=1,
                scoring_version="v1.0",
                framework_version="2017",
                input_snapshot={"scoring_input": {}},
                raw_scores={"raw": {}},
                overall_score=Decimal("40.00"),
                risk_classification=RiskClassification.HIGH,
                computed_by=USER_B,
            )
        )
        await session.flush()
        session.add(
            ScoreOverride(
                id=override_b_id,
                organization_id=ORG_B,
                assessment_id=assessment_b_id,
                snapshot_id=snapshot_b_id,
                source_revision_number=1,
                original_overall_score=Decimal("40.00"),
                original_risk_classification=RiskClassification.HIGH,
                override_overall_score=Decimal("75.00"),
                override_risk_classification=RiskClassification.MEDIUM,
                justification="Org B Manager approval",
                overridden_by=USER_B,
            )
        )
        await session.commit()

    # Verify Org A can only see its own override
    async with tenant_session(ORG_A) as session:
        result = await session.execute(select(ScoreOverride))
        overrides = result.scalars().all()
        assert len(overrides) == 1
        assert overrides[0].id == override_a_id
        assert overrides[0].organization_id == ORG_A

    # Verify Org B can only see its own override
    async with tenant_session(ORG_B) as session:
        result = await session.execute(select(ScoreOverride))
        overrides = result.scalars().all()
        assert len(overrides) == 1
        assert overrides[0].id == override_b_id
        assert overrides[0].organization_id == ORG_B


async def test_score_override_immutability_trigger() -> None:
    """Database trigger trg_prevent_score_override_mutation prevents UPDATE and DELETE."""
    assessment_id = uuid4()
    snapshot_id = uuid4()
    override_id = uuid4()

    async with tenant_session(ORG_A) as session:
        session.add(
            ComplianceAssessment(
                id=assessment_id,
                organization_id=ORG_A,
                framework_id=FRAMEWORK_ID,
                title="Assessment Immutability Test",
                status=AssessmentStatus.IN_PROGRESS,
                overall_score=Decimal("50.00"),
                risk_classification=RiskClassification.HIGH,
                created_by=USER_A,
            )
        )
        await session.flush()
        session.add(
            AssessmentScoreSnapshot(
                id=snapshot_id,
                organization_id=ORG_A,
                assessment_id=assessment_id,
                revision_number=1,
                scoring_version="v1.0",
                framework_version="2017",
                input_snapshot={},
                raw_scores={},
                overall_score=Decimal("50.00"),
                risk_classification=RiskClassification.HIGH,
                computed_by=USER_A,
            )
        )
        await session.flush()
        session.add(
            ScoreOverride(
                id=override_id,
                organization_id=ORG_A,
                assessment_id=assessment_id,
                snapshot_id=snapshot_id,
                source_revision_number=1,
                original_overall_score=Decimal("50.00"),
                original_risk_classification=RiskClassification.HIGH,
                override_overall_score=Decimal("90.00"),
                override_risk_classification=RiskClassification.LOW,
                justification="Original justification",
                overridden_by=USER_A,
            )
        )
        await session.commit()

    # Attempt UPDATE
    async with tenant_session(ORG_A) as session:
        with pytest.raises(
            (DBAPIError, ProgrammingError, IntegrityError)
        ) as exc_info:
            await session.execute(
                text(
                    "UPDATE score_overrides SET justification = 'tampered' WHERE id = :id"
                ),
                {"id": override_id},
            )
        assert "score_overrides rows are immutable" in str(
            exc_info.value
        ) or "permission denied" in str(exc_info.value)

    # Attempt DELETE
    async with tenant_session(ORG_A) as session:
        with pytest.raises(
            (DBAPIError, ProgrammingError, IntegrityError)
        ) as exc_info:
            await session.execute(
                text("DELETE FROM score_overrides WHERE id = :id"),
                {"id": override_id},
            )
        assert "score_overrides rows are immutable" in str(
            exc_info.value
        ) or "permission denied" in str(exc_info.value)


async def test_score_override_snapshot_triple_fk_constraint() -> None:
    """Database constraint fk_score_overrides_snapshot_triple rejects mismatched
    (assessment_id, revision, snapshot_id).
    """
    assessment_id = uuid4()
    snapshot_1_id = uuid4()
    snapshot_2_id = uuid4()
    mismatched_override_id = uuid4()
    matched_override_1_id = uuid4()
    matched_override_2_id = uuid4()

    async with tenant_session(ORG_A) as session:
        session.add(
            ComplianceAssessment(
                id=assessment_id,
                organization_id=ORG_A,
                framework_id=FRAMEWORK_ID,
                title="Assessment Triple FK Test",
                status=AssessmentStatus.IN_PROGRESS,
                overall_score=Decimal("70.00"),
                risk_classification=RiskClassification.MEDIUM,
                created_by=USER_A,
            )
        )
        await session.flush()
        # Revision 1
        session.add(
            AssessmentScoreSnapshot(
                id=snapshot_1_id,
                organization_id=ORG_A,
                assessment_id=assessment_id,
                revision_number=1,
                scoring_version="v1.0",
                framework_version="2017",
                input_snapshot={"rev": 1},
                raw_scores={"rev": 1},
                overall_score=Decimal("40.00"),
                risk_classification=RiskClassification.HIGH,
                computed_by=USER_A,
            )
        )
        # Revision 2
        session.add(
            AssessmentScoreSnapshot(
                id=snapshot_2_id,
                organization_id=ORG_A,
                assessment_id=assessment_id,
                revision_number=2,
                scoring_version="v1.0",
                framework_version="2017",
                input_snapshot={"rev": 2},
                raw_scores={"rev": 2},
                overall_score=Decimal("70.00"),
                risk_classification=RiskClassification.MEDIUM,
                computed_by=USER_A,
            )
        )
        await session.commit()

    # Attempt to insert an override pairing snapshot_1_id with source_revision_number=2 (mismatch!)
    async with tenant_session(ORG_A) as session:
        session.add(
            ScoreOverride(
                id=mismatched_override_id,
                organization_id=ORG_A,
                assessment_id=assessment_id,
                snapshot_id=snapshot_1_id,  # Snapshot 1 ID
                source_revision_number=2,   # But Snapshot 2 revision number!
                original_overall_score=Decimal("70.00"),
                original_risk_classification=RiskClassification.MEDIUM,
                override_overall_score=Decimal("85.00"),
                override_risk_classification=RiskClassification.LOW,
                justification="Mismatched snapshot triple attempt",
                overridden_by=USER_A,
            )
        )
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            await session.commit()
        err_msg = str(exc_info.value).lower()
        assert (
            "fk_score_overrides_snapshot_triple" in err_msg
            or "foreign key constraint" in err_msg
        )

    # Verify matched triple for revision 1 succeeds
    async with tenant_session(ORG_A) as session:
        session.add(
            ScoreOverride(
                id=matched_override_1_id,
                organization_id=ORG_A,
                assessment_id=assessment_id,
                snapshot_id=snapshot_1_id,  # Snapshot 1 ID
                source_revision_number=1,   # Matching Snapshot 1 revision number
                original_overall_score=Decimal("40.00"),
                original_risk_classification=RiskClassification.HIGH,
                override_overall_score=Decimal("80.00"),
                override_risk_classification=RiskClassification.LOW,
                justification="Valid matched override for revision 1",
                overridden_by=USER_A,
            )
        )
        await session.commit()

    # Verify matched triple for revision 2 succeeds
    async with tenant_session(ORG_A) as session:
        session.add(
            ScoreOverride(
                id=matched_override_2_id,
                organization_id=ORG_A,
                assessment_id=assessment_id,
                snapshot_id=snapshot_2_id,  # Snapshot 2 ID
                source_revision_number=2,   # Matching Snapshot 2 revision number
                original_overall_score=Decimal("70.00"),
                original_risk_classification=RiskClassification.MEDIUM,
                override_overall_score=Decimal("95.00"),
                override_risk_classification=RiskClassification.LOW,
                justification="Valid matched override for revision 2",
                overridden_by=USER_A,
            )
        )
        await session.commit()

    # Query both overrides and verify exact Decimal values
    async with tenant_session(ORG_A) as session:
        result = await session.execute(
            select(ScoreOverride)
            .where(ScoreOverride.assessment_id == assessment_id)
            .order_by(ScoreOverride.source_revision_number.asc())
        )
        overrides = result.scalars().all()
        assert len(overrides) == 2
        assert overrides[0].snapshot_id == snapshot_1_id
        assert overrides[0].source_revision_number == 1
        assert overrides[0].original_overall_score == Decimal("40.00")
        assert overrides[0].override_overall_score == Decimal("80.00")
        assert overrides[1].snapshot_id == snapshot_2_id
        assert overrides[1].source_revision_number == 2
        assert overrides[1].original_overall_score == Decimal("70.00")
        assert overrides[1].override_overall_score == Decimal("95.00")


async def test_mutation_score_invalidation_and_stale_override_lifecycle() -> None:
    """End-to-end integration test of mutation invalidation, stale overrides, and recomputation."""
    manager_principal = Principal(
        user_id=USER_A,
        organization_id=ORG_A,
        role=Role.MANAGER,
        email="manager_a@acme.test",
    )
    analyst_user = uuid4()
    async with tenant_session(ORG_A) as session:
        session.add(
            User(
                id=analyst_user,
                organization_id=ORG_A,
                email=f"analyst_a_{analyst_user.hex[:6]}@acme.test",
                role=Role.ANALYST,
            )
        )
        await session.commit()

    analyst_principal = Principal(
        user_id=analyst_user,
        organization_id=ORG_A,
        role=Role.ANALYST,
        email="analyst_a@acme.test",
    )

    # 1. Create Assessment and compute Revision 1
    assessment_id: uuid4
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        created = await service.create_assessment(
            principal=analyst_principal,
            request=ComplianceAssessmentCreateRequest(
                framework_id=FRAMEWORK_ID,
                title="E2E Lifecycle Assessment",
            ),
        )
        assessment_id = created.id
        controls = await service.get_control_assessments(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        assert len(controls) == 1
        ca_id = controls[0].id

        # Update control to SATISFIED
        await service.update_control_assessment(
            principal=analyst_principal,
            control_assessment_id=ca_id,
            request=ControlAssessmentUpdateRequest(
                status=ControlStatus.SATISFIED,
                effective_weight=Decimal("3.0"),
            ),
        )

        # Compute Revision 1
        score_1 = await service.compute_score(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        assert score_1.overall_score == Decimal("70.00")
        assert score_1.risk_classification == RiskClassification.MEDIUM
        await session.commit()

    # 2. Manager creates Score Override for Revision 1
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        override_1 = await service.create_score_override(
            principal=manager_principal,
            assessment_id=assessment_id,
            request=ScoreOverrideCreateRequest(
                override_overall_score=Decimal("80.00"),
                justification="Manager adjusted down for pending external audit",
            ),
        )
        assert override_1.source_revision_number == 1
        assert override_1.override_overall_score == Decimal("80.00")

        # Verify active override on get_assessment
        assessment_view = await service.get_assessment(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        assert assessment_view.latest_override is not None
        assert assessment_view.latest_override.override_overall_score == Decimal("80.00")
        assert assessment_view.effective_overall_score == Decimal("80.00")
        await session.commit()

    # 3. Analyst mutates control assessment -> Invalidate current computed score
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        await service.update_control_assessment(
            principal=analyst_principal,
            control_assessment_id=ca_id,
            request=ControlAssessmentUpdateRequest(
                status=ControlStatus.DEFICIENT,
            ),
        )
        await session.commit()

    # Verify assessment in DB is now invalidated
    async with tenant_session(ORG_A) as session:
        res = await session.execute(
            select(ComplianceAssessment).where(ComplianceAssessment.id == assessment_id)
        )
        db_assessment = res.scalar_one()
        assert db_assessment.overall_score is None
        assert db_assessment.risk_classification == RiskClassification.NOT_SCORED
        assert db_assessment.scoring_version is None

        # Verify get_assessment shows NO active override and NO effective score
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        assessment_view = await service.get_assessment(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        assert assessment_view.latest_override is None
        assert assessment_view.effective_overall_score is None
        assert assessment_view.effective_risk_classification == RiskClassification.NOT_SCORED

        # Verify finalization fails because state is invalidated
        with pytest.raises(ConflictError) as exc_info:
            await service.finalize_assessment(
                principal=manager_principal,
                assessment_id=assessment_id,
            )
        assert "modified since the last computation" in str(exc_info.value)

        # Verify manager cannot override invalidated score
        with pytest.raises(ConflictError) as exc_info:
            await service.create_score_override(
                principal=manager_principal,
                assessment_id=assessment_id,
                request=ScoreOverrideCreateRequest(
                    override_overall_score=Decimal("50.00"),
                    justification="Attempt override while invalidated",
                ),
            )
        assert "modified since last computation" in str(exc_info.value)

    # 4. Analyst recomputes -> Revision 2
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        score_2 = await service.compute_score(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        assert score_2.overall_score == Decimal("0.00")
        assert score_2.risk_classification == RiskClassification.CRITICAL

        # Verify Revision 1 override is superseded and NO LONGER ACTIVE
        assessment_view = await service.get_assessment(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        assert assessment_view.latest_override is None
        assert assessment_view.effective_overall_score == Decimal("0.00")
        assert assessment_view.effective_risk_classification == RiskClassification.CRITICAL

        # Historical overrides still contains Revision 1 override
        overrides = await service.list_score_overrides(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        assert len(overrides) == 1
        assert overrides[0].source_revision_number == 1
        await session.commit()

    # 5. Manager creates Score Override for Revision 2
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        override_2 = await service.create_score_override(
            principal=manager_principal,
            assessment_id=assessment_id,
            request=ScoreOverrideCreateRequest(
                override_overall_score=Decimal("45.00"),
                justification="Manager granted compensating control credit",
            ),
        )
        assert override_2.source_revision_number == 2
        assert override_2.override_overall_score == Decimal("45.00")

        # Verify Revision 2 override is now active
        assessment_view = await service.get_assessment(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        assert assessment_view.latest_override is not None
        assert assessment_view.latest_override.source_revision_number == 2
        assert assessment_view.effective_overall_score == Decimal("45.00")

        # Verify finalization succeeds with current Revision 2
        finalized = await service.finalize_assessment(
            principal=manager_principal,
            assessment_id=assessment_id,
        )
        assert finalized.status == AssessmentStatus.COMPLETED
        assert finalized.latest_override is not None
        assert finalized.effective_overall_score == Decimal("45.00")
        await session.commit()


async def test_snapshot_confidentiality_integration() -> None:
    """Verify snapshot in DB retains evidence IDs, but projection redacts them."""
    analyst_user = uuid4()
    async with tenant_session(ORG_A) as session:
        session.add(
            User(
                id=analyst_user,
                organization_id=ORG_A,
                email=f"analyst_conf_{analyst_user.hex[:6]}@acme.test",
                role=Role.ANALYST,
            )
        )
        await session.commit()

    analyst_principal = Principal(
        user_id=analyst_user,
        organization_id=ORG_A,
        role=Role.ANALYST,
        email="analyst_conf@acme.test",
    )

    doc_id = uuid4()
    async with tenant_session(ORG_A) as session:
        session.add(
            Document(
                id=doc_id,
                organization_id=ORG_A,
                filename="secret_evidence.pdf",
                storage_key="s3://cloudguard/secret_evidence.pdf",
                content_type="application/pdf",
                size_bytes=1024,
                document_type=DocumentType.POLICY,
                confidentiality_level=ConfidentialityLevel.CONFIDENTIAL,
                processing_status=ProcessingStatus.READY,
                uploader_id=analyst_user,
            )
        )
        await session.commit()

    assessment_id: uuid4
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        created = await service.create_assessment(
            principal=analyst_principal,
            request=ComplianceAssessmentCreateRequest(
                framework_id=FRAMEWORK_ID,
                title="Confidentiality Test Assessment",
            ),
        )
        assessment_id = created.id
        controls = await service.get_control_assessments(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        ca_id = controls[0].id

        # Update control and admit evidence (admitted by Manager)
        await service.update_control_assessment(
            principal=analyst_principal,
            control_assessment_id=ca_id,
            request=ControlAssessmentUpdateRequest(
                status=ControlStatus.SATISFIED,
                effective_weight=Decimal("3.0"),
            ),
        )
        manager_principal = Principal(
            user_id=USER_A,
            organization_id=ORG_A,
            role=Role.MANAGER,
            email="manager_a@acme.test",
        )
        ev_ref = await service.admit_evidence(
            principal=manager_principal,
            control_assessment_id=ca_id,
            request=EvidenceReferenceCreateRequest(
                document_id=doc_id,
            ),
        )
        secret_ev_id = ev_ref.id

        # Compute score
        score_res = await service.compute_score(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        assert score_res.overall_score == Decimal("100.00")
        await session.commit()

    # 1. Verify DB row retains exact evidence reference IDs in input_snapshot JSONB
    async with tenant_session(ORG_A) as session:
        res = await session.execute(
            select(AssessmentScoreSnapshot).where(
                AssessmentScoreSnapshot.assessment_id == assessment_id
            )
        )
        db_snap = res.scalar_one()
        assert "evidence_reference_ids" in str(db_snap.input_snapshot)
        assert str(secret_ev_id) in str(db_snap.input_snapshot)

    # 2. Verify Service list_snapshots returns safe projections without input_snapshot
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        snapshots = await service.list_snapshots(
            principal=analyst_principal,
            assessment_id=assessment_id,
        )
        assert len(snapshots) == 1
        snap_proj = snapshots[0]
        assert snap_proj.overall_score == Decimal("100.00")
        assert snap_proj.risk_classification == RiskClassification.LOW
        assert not hasattr(snap_proj, "input_snapshot")
        assert str(secret_ev_id) not in snap_proj.model_dump_json()
        assert str(doc_id) not in snap_proj.model_dump_json()
        assert "secret_evidence.pdf" not in snap_proj.model_dump_json()
