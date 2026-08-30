"""Integration tests for compliance persistence models, composite tenant FKs,
and snapshot immutability.
"""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from app.models.enums import (
    AssessmentStatus,
    ConfidentialityLevel,
    ControlStatus,
    RiskClassification,
    Role,
)
from app.repositories.database import (
    tenant_session,
    untenanted_session,
)
from app.repositories.tables import (
    AssessmentScoreSnapshot,
    ComplianceAssessment,
    ComplianceControl,
    ComplianceFramework,
    ControlAssessment,
    Document,
    DocumentChunk,
    EvidenceReference,
    Organization,
    User,
)
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

pytestmark = pytest.mark.integration

ORG_A = uuid4()
ORG_B = uuid4()
USER_A = uuid4()
USER_B = uuid4()
FRAMEWORK_ID = uuid4()
CONTROL_1_ID = uuid4()
CONTROL_2_ID = uuid4()
DOC_A = uuid4()
CHUNK_A = uuid4()
DOC_B = uuid4()
CHUNK_B = uuid4()


def _skip_without_database() -> None:
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 with PostgreSQL running")


@pytest.fixture(scope="module", autouse=True)
async def seed_compliance_test_data() -> object:
    _skip_without_database()

    # Untenanted: Seed organizations, global framework, and controls
    async with untenanted_session() as session:
        for org_id, label in ((ORG_A, "alpha-corp"), (ORG_B, "beta-corp")):
            org = Organization(
                id=org_id,
                name=label.title(),
                slug=f"{label}-{org_id.hex[:8]}",
            )
            session.add(org)

        fw = ComplianceFramework(
            id=FRAMEWORK_ID,
            code=f"SOC2_{FRAMEWORK_ID.hex[:8]}",
            name="SOC 2 Type II Test",
            version="2026.1",
            description="Test framework for compliance scoring",
        )
        session.add(fw)
        await session.commit()

    async with untenanted_session() as session:
        c1 = ComplianceControl(
            id=CONTROL_1_ID,
            framework_id=FRAMEWORK_ID,
            control_code="CC6.1",
            title="Logical Access Controls",
            category="Security",
            default_weight=Decimal("3.0"),
        )
        c2 = ComplianceControl(
            id=CONTROL_2_ID,
            framework_id=FRAMEWORK_ID,
            control_code="CC6.2",
            title="User Registration",
            category="Security",
            default_weight=Decimal("5.0"),
        )
        session.add_all([c1, c2])
        await session.commit()

    # Seed users and document for Tenant A
    async with tenant_session(ORG_A) as session:
        user_a = User(
            id=USER_A,
            organization_id=ORG_A,
            email="analyst@alpha.test",
            role=Role.ANALYST,
        )
        session.add(user_a)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        doc_a = Document(
            id=DOC_A,
            organization_id=ORG_A,
            uploader_id=USER_A,
            filename="alpha_policy.pdf",
            storage_key="raw/alpha_policy.pdf",
            content_type="application/pdf",
        )
        session.add(doc_a)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        chunk_a = DocumentChunk(
            id=CHUNK_A,
            organization_id=ORG_A,
            document_id=DOC_A,
            chunk_index=0,
            content="Access controls must be reviewed quarterly.",
        )
        session.add(chunk_a)
        await session.commit()

    # Seed users and document for Tenant B
    async with tenant_session(ORG_B) as session:
        user_b = User(
            id=USER_B,
            organization_id=ORG_B,
            email="analyst@beta.test",
            role=Role.ANALYST,
        )
        session.add(user_b)
        await session.commit()

    async with tenant_session(ORG_B) as session:
        doc_b = Document(
            id=DOC_B,
            organization_id=ORG_B,
            uploader_id=USER_B,
            filename="beta_policy.pdf",
            storage_key="raw/beta_policy.pdf",
            content_type="application/pdf",
        )
        session.add(doc_b)
        await session.commit()

    async with tenant_session(ORG_B) as session:
        chunk_b = DocumentChunk(
            id=CHUNK_B,
            organization_id=ORG_B,
            document_id=DOC_B,
            chunk_index=0,
            content="Beta access control standard.",
        )
        session.add(chunk_b)
        await session.commit()

    return None


async def test_global_framework_and_controls_accessible_across_tenants() -> None:
    _skip_without_database()

    async with tenant_session(ORG_A) as session:
        fw_a = await session.get(ComplianceFramework, FRAMEWORK_ID)
        assert fw_a is not None
        assert fw_a.code.startswith("SOC2_")

    async with tenant_session(ORG_B) as session:
        fw_b = await session.get(ComplianceFramework, FRAMEWORK_ID)
        assert fw_b is not None
        assert fw_b.code.startswith("SOC2_")


async def test_compliance_assessment_tenant_rls_isolation() -> None:
    _skip_without_database()

    assessment_id = uuid4()

    # Tenant A creates assessment
    async with tenant_session(ORG_A) as session:
        assessment = ComplianceAssessment(
            id=assessment_id,
            organization_id=ORG_A,
            framework_id=FRAMEWORK_ID,
            title="Alpha Corp Annual Assessment",
            status=AssessmentStatus.DRAFT,
            overall_score=Decimal("85.50"),
            risk_classification=RiskClassification.LOW,
            scoring_version="v1.0",
            created_by=USER_A,
        )
        session.add(assessment)
        await session.commit()

    # Tenant A reads assessment
    async with tenant_session(ORG_A) as session:
        found = await session.get(ComplianceAssessment, assessment_id)
        assert found is not None
        assert found.overall_score == Decimal("85.50")
        assert found.risk_classification == RiskClassification.LOW

    # Tenant B cannot read Tenant A assessment
    async with tenant_session(ORG_B) as session:
        found = await session.get(ComplianceAssessment, assessment_id)
        assert found is None

        # Bare SELECT without tenant predicate still returns zero rows for Tenant B
        res = await session.scalars(
            select(ComplianceAssessment).where(ComplianceAssessment.id == assessment_id)
        )
        assert res.first() is None


async def test_control_assessment_and_unique_constraint() -> None:
    _skip_without_database()

    assessment_id = uuid4()
    ca_id = uuid4()

    async with tenant_session(ORG_A) as session:
        assessment = ComplianceAssessment(
            id=assessment_id,
            organization_id=ORG_A,
            framework_id=FRAMEWORK_ID,
            title="Alpha Control Assessment Test",
            status=AssessmentStatus.IN_PROGRESS,
            created_by=USER_A,
        )
        session.add(assessment)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        ca = ControlAssessment(
            id=ca_id,
            organization_id=ORG_A,
            assessment_id=assessment_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("3.0"),
            rationale="Quarterly reviews documented in policy",
        )
        session.add(ca)
        await session.commit()

    # Duplicate control assessment for same assessment and control must fail
    async with tenant_session(ORG_A) as session:
        duplicate_ca = ControlAssessment(
            id=uuid4(),
            organization_id=ORG_A,
            assessment_id=assessment_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.DEFICIENT,
            effective_weight=Decimal("3.0"),
        )
        session.add(duplicate_ca)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_evidence_reference_persistence_and_rls() -> None:
    _skip_without_database()

    assessment_id = uuid4()
    ca_id = uuid4()
    evidence_id = uuid4()

    async with tenant_session(ORG_A) as session:
        assessment = ComplianceAssessment(
            id=assessment_id,
            organization_id=ORG_A,
            framework_id=FRAMEWORK_ID,
            title="Alpha Evidence Test",
            created_by=USER_A,
        )
        session.add(assessment)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        ca = ControlAssessment(
            id=ca_id,
            organization_id=ORG_A,
            assessment_id=assessment_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("3.0"),
        )
        session.add(ca)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        ev = EvidenceReference(
            id=evidence_id,
            organization_id=ORG_A,
            control_assessment_id=ca_id,
            document_id=DOC_A,
            chunk_id=CHUNK_A,
            confidentiality_level=ConfidentialityLevel.INTERNAL,
            snippet="Access controls must be reviewed quarterly.",
            created_by=USER_A,
        )
        session.add(ev)
        await session.commit()

    # Tenant A reads evidence
    async with tenant_session(ORG_A) as session:
        found_ev = await session.get(EvidenceReference, evidence_id)
        assert found_ev is not None
        assert found_ev.document_id == DOC_A
        assert found_ev.chunk_id == CHUNK_A

    # Tenant B cannot read Tenant A evidence
    async with tenant_session(ORG_B) as session:
        found_ev = await session.get(EvidenceReference, evidence_id)
        assert found_ev is None


async def test_delete_chunk_sets_chunk_id_null_in_evidence_reference() -> None:
    """Deleting a referenced chunk clears chunk_id while preserving evidence row and
    organization_id.
    """
    _skip_without_database()

    doc_id = uuid4()
    chunk_id = uuid4()
    assessment_id = uuid4()
    ca_id = uuid4()
    evidence_id = uuid4()

    async with tenant_session(ORG_A) as session:
        doc = Document(
            id=doc_id,
            organization_id=ORG_A,
            uploader_id=USER_A,
            filename="chunk_delete_test.pdf",
            storage_key="raw/chunk_delete_test.pdf",
            content_type="application/pdf",
        )
        session.add(doc)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        chunk = DocumentChunk(
            id=chunk_id,
            organization_id=ORG_A,
            document_id=doc_id,
            chunk_index=0,
            content="Temporary chunk to be deleted.",
        )
        session.add(chunk)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        assessment = ComplianceAssessment(
            id=assessment_id,
            organization_id=ORG_A,
            framework_id=FRAMEWORK_ID,
            title="Chunk Delete Assessment Test",
            created_by=USER_A,
        )
        session.add(assessment)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        ca = ControlAssessment(
            id=ca_id,
            organization_id=ORG_A,
            assessment_id=assessment_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
        )
        session.add(ca)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        ev = EvidenceReference(
            id=evidence_id,
            organization_id=ORG_A,
            control_assessment_id=ca_id,
            document_id=doc_id,
            chunk_id=chunk_id,
            confidentiality_level=ConfidentialityLevel.INTERNAL,
            snippet="Temporary chunk to be deleted.",
            created_by=USER_A,
        )
        session.add(ev)
        await session.commit()

    # Verify initial state: evidence references chunk_id
    async with tenant_session(ORG_A) as session:
        ev_before = await session.get(EvidenceReference, evidence_id)
        assert ev_before is not None
        assert ev_before.chunk_id == chunk_id
        assert ev_before.organization_id == ORG_A

    # Delete the chunk
    async with tenant_session(ORG_A) as session:
        chunk_to_delete = await session.get(DocumentChunk, chunk_id)
        assert chunk_to_delete is not None
        await session.delete(chunk_to_delete)
        await session.commit()

    # Verify after chunk deletion:
    # 1. evidence_references row remains
    # 2. organization_id is unchanged (ORG_A)
    # 3. chunk_id is set to None (NULL)
    # 4. document_id remains doc_id
    async with tenant_session(ORG_A) as session:
        ev_after = await session.get(EvidenceReference, evidence_id)
        assert ev_after is not None
        assert ev_after.organization_id == ORG_A
        assert ev_after.document_id == doc_id
        assert ev_after.chunk_id is None


# -----------------------------------------------------------------------------
# DEFECT 1: Score Snapshot Immutability Tests (Append-Only Enforcement)
# -----------------------------------------------------------------------------


async def test_snapshot_insert_and_select_succeeds() -> None:
    """Under cloudguard_app role, inserting and selecting snapshots succeeds."""
    _skip_without_database()

    assessment_id = uuid4()
    snap_id = uuid4()

    async with tenant_session(ORG_A) as session:
        assessment = ComplianceAssessment(
            id=assessment_id,
            organization_id=ORG_A,
            framework_id=FRAMEWORK_ID,
            title="Alpha Snapshot Immutability Test",
            overall_score=Decimal("80.00"),
            risk_classification=RiskClassification.LOW,
            created_by=USER_A,
        )
        session.add(assessment)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        snap = AssessmentScoreSnapshot(
            id=snap_id,
            organization_id=ORG_A,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={"controls": []},
            raw_scores={},
            overall_score=Decimal("80.00"),
            risk_classification=RiskClassification.LOW,
            computed_by=USER_A,
        )
        session.add(snap)
        await session.commit()

    # SELECT succeeds
    async with tenant_session(ORG_A) as session:
        saved = await session.get(AssessmentScoreSnapshot, snap_id)
        assert saved is not None
        assert saved.overall_score == Decimal("80.00")
        assert saved.risk_classification == RiskClassification.LOW


async def test_snapshot_update_fails() -> None:
    """Under cloudguard_app role, UPDATE on existing snapshot must fail."""
    _skip_without_database()

    assessment_id = uuid4()
    snap_id = uuid4()

    async with tenant_session(ORG_A) as session:
        assessment = ComplianceAssessment(
            id=assessment_id,
            organization_id=ORG_A,
            framework_id=FRAMEWORK_ID,
            title="Alpha Snapshot Update Test",
            created_by=USER_A,
        )
        session.add(assessment)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        snap = AssessmentScoreSnapshot(
            id=snap_id,
            organization_id=ORG_A,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("75.00"),
            risk_classification=RiskClassification.MEDIUM,
            computed_by=USER_A,
        )
        session.add(snap)
        await session.commit()

    # Attempt direct UPDATE via SQL / ORM
    async with tenant_session(ORG_A) as session:
        with pytest.raises((DBAPIError, ProgrammingError, IntegrityError)):
            await session.execute(
                text(
                    "UPDATE assessment_score_snapshots "
                    "SET overall_score = 99.99 "
                    "WHERE id = :id"
                ),
                {"id": snap_id},
            )
            await session.commit()


async def test_snapshot_delete_fails() -> None:
    """Under cloudguard_app role, DELETE on existing snapshot must fail."""
    _skip_without_database()

    assessment_id = uuid4()
    snap_id = uuid4()

    async with tenant_session(ORG_A) as session:
        assessment = ComplianceAssessment(
            id=assessment_id,
            organization_id=ORG_A,
            framework_id=FRAMEWORK_ID,
            title="Alpha Snapshot Delete Test",
            created_by=USER_A,
        )
        session.add(assessment)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        snap = AssessmentScoreSnapshot(
            id=snap_id,
            organization_id=ORG_A,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("75.00"),
            risk_classification=RiskClassification.MEDIUM,
            computed_by=USER_A,
        )
        session.add(snap)
        await session.commit()

    # Attempt direct DELETE
    async with tenant_session(ORG_A) as session:
        with pytest.raises((DBAPIError, ProgrammingError, IntegrityError)):
            await session.execute(
                text(
                    "DELETE FROM assessment_score_snapshots WHERE id = :id"
                ),
                {"id": snap_id},
            )
            await session.commit()


async def test_duplicate_snapshot_revision_fails() -> None:
    """Duplicate revision_number on the same assessment must fail."""
    _skip_without_database()

    assessment_id = uuid4()

    async with tenant_session(ORG_A) as session:
        assessment = ComplianceAssessment(
            id=assessment_id,
            organization_id=ORG_A,
            framework_id=FRAMEWORK_ID,
            title="Alpha Snapshot Duplicate Rev Test",
            created_by=USER_A,
        )
        session.add(assessment)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        snap1 = AssessmentScoreSnapshot(
            id=uuid4(),
            organization_id=ORG_A,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("75.00"),
            risk_classification=RiskClassification.MEDIUM,
        )
        session.add(snap1)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        snap2 = AssessmentScoreSnapshot(
            id=uuid4(),
            organization_id=ORG_A,
            assessment_id=assessment_id,
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("90.00"),
            risk_classification=RiskClassification.LOW,
        )
        session.add(snap2)
        with pytest.raises(IntegrityError):
            await session.commit()


# -----------------------------------------------------------------------------
# DEFECT 2: Cross-Tenant Foreign Key & Consistency Integrity Tests
# -----------------------------------------------------------------------------


async def test_cross_tenant_control_assessment_insert_fails() -> None:
    """Tenant A control_assessment referencing Tenant B assessment must fail at DB boundary."""
    _skip_without_database()

    assessment_b_id = uuid4()

    # Tenant B creates assessment
    async with tenant_session(ORG_B) as session:
        assessment_b = ComplianceAssessment(
            id=assessment_b_id,
            organization_id=ORG_B,
            framework_id=FRAMEWORK_ID,
            title="Beta Assessment",
            created_by=USER_B,
        )
        session.add(assessment_b)
        await session.commit()

    # Tenant A attempts to insert control_assessment pointing to Tenant B's assessment
    async with tenant_session(ORG_A) as session:
        bad_ca = ControlAssessment(
            id=uuid4(),
            organization_id=ORG_A,
            assessment_id=assessment_b_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
        )
        session.add(bad_ca)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_cross_tenant_evidence_reference_to_document_fails() -> None:
    """Tenant A evidence reference pointing to Tenant B document must fail at DB boundary."""
    _skip_without_database()

    assessment_a_id = uuid4()
    ca_a_id = uuid4()

    async with tenant_session(ORG_A) as session:
        assessment_a = ComplianceAssessment(
            id=assessment_a_id,
            organization_id=ORG_A,
            framework_id=FRAMEWORK_ID,
            title="Alpha Cross-Tenant Doc Test",
            created_by=USER_A,
        )
        session.add(assessment_a)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        ca_a = ControlAssessment(
            id=ca_a_id,
            organization_id=ORG_A,
            assessment_id=assessment_a_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
        )
        session.add(ca_a)
        await session.commit()

    # Tenant A creates evidence referencing Tenant B document (DOC_B)
    async with tenant_session(ORG_A) as session:
        bad_ev = EvidenceReference(
            id=uuid4(),
            organization_id=ORG_A,
            control_assessment_id=ca_a_id,
            document_id=DOC_B,  # Owned by ORG_B
            chunk_id=None,
            created_by=USER_A,
        )
        session.add(bad_ev)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_cross_tenant_evidence_reference_to_chunk_fails() -> None:
    """Tenant A evidence reference pointing to Tenant B chunk must fail at DB boundary."""
    _skip_without_database()

    assessment_a_id = uuid4()
    ca_a_id = uuid4()

    async with tenant_session(ORG_A) as session:
        assessment_a = ComplianceAssessment(
            id=assessment_a_id,
            organization_id=ORG_A,
            framework_id=FRAMEWORK_ID,
            title="Alpha Cross-Tenant Chunk Test",
            created_by=USER_A,
        )
        session.add(assessment_a)
        await session.commit()

    async with tenant_session(ORG_A) as session:
        ca_a = ControlAssessment(
            id=ca_a_id,
            organization_id=ORG_A,
            assessment_id=assessment_a_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
        )
        session.add(ca_a)
        await session.commit()

    # Tenant A creates evidence with valid DOC_A but referencing Tenant B chunk (CHUNK_B)
    async with tenant_session(ORG_A) as session:
        bad_ev = EvidenceReference(
            id=uuid4(),
            organization_id=ORG_A,
            control_assessment_id=ca_a_id,
            document_id=DOC_A,
            chunk_id=CHUNK_B,  # Owned by ORG_B
            created_by=USER_A,
        )
        session.add(bad_ev)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_cross_tenant_evidence_reference_to_control_assessment_fails() -> None:
    """Tenant A evidence reference pointing to Tenant B control_assessment must fail."""
    _skip_without_database()

    assessment_b_id = uuid4()
    ca_b_id = uuid4()

    # Tenant B creates assessment and control_assessment
    async with tenant_session(ORG_B) as session:
        assessment_b = ComplianceAssessment(
            id=assessment_b_id,
            organization_id=ORG_B,
            framework_id=FRAMEWORK_ID,
            title="Beta CA Test",
            created_by=USER_B,
        )
        session.add(assessment_b)
        await session.commit()

    async with tenant_session(ORG_B) as session:
        ca_b = ControlAssessment(
            id=ca_b_id,
            organization_id=ORG_B,
            assessment_id=assessment_b_id,
            control_id=CONTROL_1_ID,
            status=ControlStatus.SATISFIED,
        )
        session.add(ca_b)
        await session.commit()

    # Tenant A attempts to add evidence pointing to Tenant B's control assessment
    async with tenant_session(ORG_A) as session:
        bad_ev = EvidenceReference(
            id=uuid4(),
            organization_id=ORG_A,
            control_assessment_id=ca_b_id,  # Owned by ORG_B
            document_id=DOC_A,
            chunk_id=CHUNK_A,
            created_by=USER_A,
        )
        session.add(bad_ev)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_cross_tenant_score_snapshot_insert_fails() -> None:
    """Tenant A score snapshot referencing Tenant B assessment must fail at DB boundary."""
    _skip_without_database()

    assessment_b_id = uuid4()

    async with tenant_session(ORG_B) as session:
        assessment_b = ComplianceAssessment(
            id=assessment_b_id,
            organization_id=ORG_B,
            framework_id=FRAMEWORK_ID,
            title="Beta Assessment for Snap Test",
            created_by=USER_B,
        )
        session.add(assessment_b)
        await session.commit()

    # Tenant A attempts to create snapshot for Tenant B assessment
    async with tenant_session(ORG_A) as session:
        bad_snap = AssessmentScoreSnapshot(
            id=uuid4(),
            organization_id=ORG_A,
            assessment_id=assessment_b_id,  # Owned by ORG_B
            revision_number=1,
            scoring_version="v1.0",
            framework_version="2026.1",
            input_snapshot={},
            raw_scores={},
            overall_score=Decimal("80.00"),
            risk_classification=RiskClassification.LOW,
            computed_by=USER_A,
        )
        session.add(bad_snap)
        with pytest.raises(IntegrityError):
            await session.commit()
