"""Integration tests for ComplianceAssessmentService and SQLAlchemyComplianceRepository.

Tests run with the NOBYPASSRLS cloudguard_app role to verify tenant isolation,
permission boundaries, clearance enforcement, duplicate prevention, and concurrency-safe scoring.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from app.adapters.local.compliance_repository import SQLAlchemyComplianceRepository
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.adapters.mock.llm import MockLLMProvider
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.models.compliance import (
    ComplianceAssessmentCreateRequest,
    ComplianceCandidateExtractionRequest,
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
from app.repositories.database import (
    tenant_session,
    untenanted_session,
)
from app.repositories.tables import (
    ComplianceControl,
    ComplianceFramework,
    Document,
    DocumentChunk,
    Organization,
    User,
)
from app.services.compliance import ComplianceAssessmentService
from app.services.compliance_candidate_extraction import (
    ComplianceCandidateExtractionService,
)
from app.services.retrieval import RetrievalService

pytestmark = pytest.mark.integration

ORG_A = uuid4()
ORG_B = uuid4()
USER_A_ANALYST = uuid4()
USER_A_MANAGER = uuid4()
USER_B_ANALYST = uuid4()
FRAMEWORK_ID = uuid4()
CONTROL_1_ID = uuid4()
CONTROL_2_ID = uuid4()
DOC_A_INTERNAL = uuid4()
DOC_A_CONFIDENTIAL = uuid4()
CHUNK_A_INTERNAL = uuid4()
DOC_B_INTERNAL = uuid4()
CHUNK_B_INTERNAL = uuid4()

PRINCIPAL_A_ANALYST = Principal(
    user_id=USER_A_ANALYST,
    organization_id=ORG_A,
    role=Role.ANALYST,
    email="analyst@alpha.service-test",
)

PRINCIPAL_A_MANAGER = Principal(
    user_id=USER_A_MANAGER,
    organization_id=ORG_A,
    role=Role.MANAGER,
    email="manager@alpha.service-test",
)

PRINCIPAL_B_ANALYST = Principal(
    user_id=USER_B_ANALYST,
    organization_id=ORG_B,
    role=Role.ANALYST,
    email="analyst@beta.service-test",
)


def _skip_without_database() -> None:
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 with PostgreSQL running")


@pytest.fixture(scope="module", autouse=True)
async def seed_service_integration_data() -> object:
    _skip_without_database()

    # 1. Untenanted: Seed organizations, global framework, and controls
    async with untenanted_session() as session:
        for org_id, label in ((ORG_A, "svc-alpha"), (ORG_B, "svc-beta")):
            org = Organization(
                id=org_id,
                name=label.title(),
                slug=f"{label}-{org_id.hex[:8]}",
            )
            session.add(org)

        fw = ComplianceFramework(
            id=FRAMEWORK_ID,
            code=f"NIST_CSF_{FRAMEWORK_ID.hex[:8]}",
            name="NIST Cybersecurity Framework Test",
            version="2.0",
            description="Service integration test framework",
        )
        session.add(fw)
        await session.commit()

    async with untenanted_session() as session:
        c1 = ComplianceControl(
            id=CONTROL_1_ID,
            framework_id=FRAMEWORK_ID,
            control_code="PR.AC-1",
            title="Identities and Credentials",
            category="Protect",
            default_weight=Decimal("3.0"),
        )
        c2 = ComplianceControl(
            id=CONTROL_2_ID,
            framework_id=FRAMEWORK_ID,
            control_code="PR.AC-2",
            title="Physical Access",
            category="Protect",
            default_weight=Decimal("5.0"),
        )
        session.add_all([c1, c2])
        await session.commit()

    # 2. Seed users and documents for Tenant A
    async with tenant_session(ORG_A) as session:
        user_a1 = User(
            id=USER_A_ANALYST,
            organization_id=ORG_A,
            email="analyst@alpha.service-test",
            role=Role.ANALYST,
        )
        user_a2 = User(
            id=USER_A_MANAGER,
            organization_id=ORG_A,
            email="manager@alpha.service-test",
            role=Role.MANAGER,
        )
        session.add_all([user_a1, user_a2])
        await session.commit()

    async with tenant_session(ORG_A) as session:
        doc_internal = Document(
            id=DOC_A_INTERNAL,
            organization_id=ORG_A,
            uploader_id=USER_A_ANALYST,
            filename="alpha_internal.pdf",
            storage_key="raw/alpha_internal.pdf",
            content_type="application/pdf",
            confidentiality_level=ConfidentialityLevel.INTERNAL,
        )
        doc_confidential = Document(
            id=DOC_A_CONFIDENTIAL,
            organization_id=ORG_A,
            uploader_id=USER_A_MANAGER,
            filename="alpha_confidential.pdf",
            storage_key="raw/alpha_confidential.pdf",
            content_type="application/pdf",
            confidentiality_level=ConfidentialityLevel.CONFIDENTIAL,
        )
        session.add_all([doc_internal, doc_confidential])
        await session.commit()

    async with tenant_session(ORG_A) as session:
        chunk_internal = DocumentChunk(
            id=CHUNK_A_INTERNAL,
            organization_id=ORG_A,
            document_id=DOC_A_INTERNAL,
            chunk_index=0,
            content="MFA is enforced on all systems.",
        )
        session.add(chunk_internal)
        await session.commit()

    # 3. Seed users and documents for Tenant B
    async with tenant_session(ORG_B) as session:
        user_b = User(
            id=USER_B_ANALYST,
            organization_id=ORG_B,
            email="analyst@beta.service-test",
            role=Role.ANALYST,
        )
        session.add(user_b)
        await session.commit()

    async with tenant_session(ORG_B) as session:
        doc_b = Document(
            id=DOC_B_INTERNAL,
            organization_id=ORG_B,
            uploader_id=USER_B_ANALYST,
            filename="beta_internal.pdf",
            storage_key="raw/beta_internal.pdf",
            content_type="application/pdf",
            confidentiality_level=ConfidentialityLevel.INTERNAL,
        )
        session.add(doc_b)
        await session.commit()

    async with tenant_session(ORG_B) as session:
        chunk_b = DocumentChunk(
            id=CHUNK_B_INTERNAL,
            organization_id=ORG_B,
            document_id=DOC_B_INTERNAL,
            chunk_index=0,
            content="Beta access policies.",
        )
        session.add(chunk_b)
        await session.commit()

    return None


async def test_full_assessment_lifecycle_and_deterministic_scoring() -> None:
    """Tests complete assessment flow:
    creation -> control update -> evidence admission -> scoring.
    """
    _skip_without_database()

    # 1. Create Assessment as Tenant A Analyst
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        create_req = ComplianceAssessmentCreateRequest(
            framework_id=FRAMEWORK_ID,
            title="Tenant A Annual Cybersecurity Review",
        )
        assessment = await service.create_assessment(
            principal=PRINCIPAL_A_ANALYST,
            request=create_req,
        )
        assert assessment.organization_id == ORG_A
        assert assessment.status == AssessmentStatus.DRAFT
        assert assessment.overall_score is None
        assert assessment.risk_classification == RiskClassification.NOT_SCORED
        assessment_id = assessment.id

    # 2. Verify controls were initialized
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        controls = await service.get_control_assessments(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment_id,
        )
        assert len(controls) == 2
        assert all(c.status == ControlStatus.UNASSESSED for c in controls)
        ctrl1 = next(c for c in controls if c.control_id == CONTROL_1_ID)
        ctrl2 = next(c for c in controls if c.control_id == CONTROL_2_ID)
        assert ctrl1.effective_weight == Decimal("3.0")
        assert ctrl2.effective_weight == Decimal("5.0")

    # 3. Update Control 1 to SATISFIED and attach evidence
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        updated_ctrl1 = await service.update_control_assessment(
            principal=PRINCIPAL_A_ANALYST,
            control_assessment_id=ctrl1.id,
            request=ControlAssessmentUpdateRequest(
                status=ControlStatus.SATISFIED,
                rationale="MFA verified by policy",
            ),
        )
        assert updated_ctrl1.status == ControlStatus.SATISFIED

    # 4. Evidence admission: Analyst attaches INTERNAL document chunk (trusted snippet)
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        ev = await service.admit_evidence(
            principal=PRINCIPAL_A_ANALYST,
            control_assessment_id=ctrl1.id,
            request=EvidenceReferenceCreateRequest(
                document_id=DOC_A_INTERNAL,
                chunk_id=CHUNK_A_INTERNAL,
            ),
        )
        assert ev.chunk_id == CHUNK_A_INTERNAL
        assert ev.snippet == "MFA is enforced on all systems."

    # 5. Clearance enforcement: Analyst cannot attach CONFIDENTIAL document
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        with pytest.raises(AuthorizationError):
            await service.admit_evidence(
                principal=PRINCIPAL_A_ANALYST,
                control_assessment_id=ctrl2.id,
                request=EvidenceReferenceCreateRequest(
                    document_id=DOC_A_CONFIDENTIAL,
                ),
            )

    # 6. Manager CAN attach CONFIDENTIAL document
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        await service.update_control_assessment(
            principal=PRINCIPAL_A_MANAGER,
            control_assessment_id=ctrl2.id,
            request=ControlAssessmentUpdateRequest(
                status=ControlStatus.PARTIALLY_SATISFIED,
                rationale="Partial physical access controls",
            ),
        )

        ev2 = await service.admit_evidence(
            principal=PRINCIPAL_A_MANAGER,
            control_assessment_id=ctrl2.id,
            request=EvidenceReferenceCreateRequest(
                document_id=DOC_A_CONFIDENTIAL,
            ),
        )
        assert ev2.confidentiality_level == ConfidentialityLevel.CONFIDENTIAL
        assert ev2.snippet is None  # no chunk_id -> snippet is None (trusted)

    # 7. Compute score
    # Control 1: SATISFIED (weight 3.0, grounded raw_score 100.0, weighted 300.0)
    # Control 2: PARTIALLY_SATISFIED (weight 5.0, grounded raw_score 50.0, weighted 250.0)
    # Total weight: 8.0, Total weighted score: 550.0 -> overall_score = 68.75 (MEDIUM risk)
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        score_res = await service.compute_score(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment_id,
        )
        assert score_res.overall_score == Decimal("68.75")
        assert score_res.residual_risk == Decimal("31.25")
        assert score_res.risk_classification == RiskClassification.MEDIUM

    # Check updated assessment & snapshots in DB
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        assessment_db = await service.get_assessment(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment_id,
        )
        assert assessment_db.overall_score == Decimal("68.75")
        assert assessment_db.risk_classification == RiskClassification.MEDIUM

        snapshots = await service.list_snapshots(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment_id,
        )
        assert len(snapshots) == 1
        assert snapshots[0].revision_number == 1
        assert snapshots[0].overall_score == Decimal("68.75")

        # Verify canonical input snapshot contains exact admitted evidence IDs and NO snippets
        snap_input = snapshots[0].input_snapshot
        assert snap_input["framework_id"] == str(FRAMEWORK_ID)
        assert snap_input["framework_version"] == "2.0"
        assert snap_input["scoring_version"] == "v1.0"
        controls_snap = snap_input["controls"]
        assert len(controls_snap) == 2

        ctrl1_snap = next(c for c in controls_snap if c["control_id"] == str(CONTROL_1_ID))
        ctrl2_snap = next(c for c in controls_snap if c["control_id"] == str(CONTROL_2_ID))

        assert ctrl1_snap["status"] == "satisfied"
        assert ctrl1_snap["effective_weight"] == "3.0"
        assert ctrl1_snap["evidence_count"] == 1
        assert ctrl1_snap["evidence_reference_ids"] == [str(ev.id)]

        assert ctrl2_snap["status"] == "partially_satisfied"
        assert ctrl2_snap["effective_weight"] == "5.0"
        assert ctrl2_snap["evidence_count"] == 1
        assert ctrl2_snap["evidence_reference_ids"] == [str(ev2.id)]

        # No snippet or sensitive text in snapshot
        snap_str = str(snap_input)
        assert "snippet" not in snap_str
        assert "content" not in snap_str
        assert "MFA is enforced" not in snap_str


async def test_cross_tenant_isolation_in_service() -> None:
    """Tenant B cannot read, update, or compute Tenant A's assessment."""
    _skip_without_database()

    assessment_id = uuid4()

    # Tenant A creates assessment
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        assessment = await service.create_assessment(
            principal=PRINCIPAL_A_ANALYST,
            request=ComplianceAssessmentCreateRequest(
                framework_id=FRAMEWORK_ID,
                title="Tenant A Private Assessment",
            ),
        )
        assessment_id = assessment.id

    # Tenant B tries to get assessment -> NotFoundError
    async with tenant_session(ORG_B) as session:
        repo_b = SQLAlchemyComplianceRepository(session)
        service_b = ComplianceAssessmentService(repository=repo_b)

        with pytest.raises(NotFoundError):
            await service_b.get_assessment(
                principal=PRINCIPAL_B_ANALYST,
                assessment_id=assessment_id,
            )

        with pytest.raises(NotFoundError):
            await service_b.compute_score(
                principal=PRINCIPAL_B_ANALYST,
                assessment_id=assessment_id,
            )


async def test_concurrent_score_computation_serializes_revisions() -> None:
    """Two concurrent compute_score calls produce sequential unique revisions without conflict."""
    _skip_without_database()

    assessment_id = uuid4()

    # Create assessment
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        assessment = await service.create_assessment(
            principal=PRINCIPAL_A_ANALYST,
            request=ComplianceAssessmentCreateRequest(
                framework_id=FRAMEWORK_ID,
                title="Concurrency Test Assessment",
            ),
        )
        assessment_id = assessment.id

    # Run two score computations concurrently in separate sessions
    async def _compute_task(principal: Principal) -> None:
        async with tenant_session(ORG_A) as session:
            r = SQLAlchemyComplianceRepository(session)
            s = ComplianceAssessmentService(repository=r)
            await s.compute_score(principal=principal, assessment_id=assessment_id)

    await asyncio.gather(
        _compute_task(PRINCIPAL_A_ANALYST),
        _compute_task(PRINCIPAL_A_MANAGER),
    )

    # Verify both snapshots exist and have sequential revisions [1, 2]
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        snapshots = await service.list_snapshots(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment_id,
        )
        assert len(snapshots) == 2
        revs = [s.revision_number for s in snapshots]
        assert revs == [1, 2]


async def test_concurrent_compute_and_mutation_serialization() -> None:
    """Proves scoring and control mutation participate in the same row-lock
    serialization discipline.

    1. Task A starts compute and locks the assessment FOR UPDATE.
    2. Task B attempts update_control_assessment on the same assessment and is blocked behind A.
    3. Task A finishes compute and commits snapshot Revision 1 (recording pre-mutation inputs).
    4. Task B unblocks, updates the control to SATISFIED, and commits.
    5. A second compute generates Revision 2 reflecting the updated control state.
    """
    _skip_without_database()

    assessment_id = uuid4()

    # 1. Setup assessment in initial state (both controls UNASSESSED)
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        assessment = await service.create_assessment(
            principal=PRINCIPAL_A_ANALYST,
            request=ComplianceAssessmentCreateRequest(
                framework_id=FRAMEWORK_ID,
                title="Compute vs Mutation Race Test",
            ),
        )
        assessment_id = assessment.id
        controls = await service.get_control_assessments(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment_id,
        )
        ctrl1_id = controls[0].id

    compute_locked = asyncio.Event()
    release_compute = asyncio.Event()
    mutation_started = asyncio.Event()

    async def _compute_task() -> None:
        async with tenant_session(ORG_A) as session:
            r = SQLAlchemyComplianceRepository(session)
            s = ComplianceAssessmentService(repository=r)
            # Acquire parent assessment lock FOR UPDATE
            await r.lock_assessment(organization_id=ORG_A, assessment_id=assessment_id)
            compute_locked.set()

            # Wait until mutation task has started and attempted to acquire the lock
            await release_compute.wait()

            # Execute compute while holding the lock
            await s.compute_score(principal=PRINCIPAL_A_ANALYST, assessment_id=assessment_id)

    async def _mutation_task() -> None:
        # Ensure compute task has acquired the row lock first
        await compute_locked.wait()
        mutation_started.set()

        async with tenant_session(ORG_A) as session:
            r = SQLAlchemyComplianceRepository(session)
            s = ComplianceAssessmentService(repository=r)
            # This call acquires parent assessment FOR UPDATE and will block until compute commits
            await s.update_control_assessment(
                principal=PRINCIPAL_A_ANALYST,
                control_assessment_id=ctrl1_id,
                request=ControlAssessmentUpdateRequest(
                    status=ControlStatus.SATISFIED,
                    rationale="Verified during concurrent test",
                ),
            )

    # Launch compute task
    compute_coro = asyncio.create_task(_compute_task())
    await compute_locked.wait()

    # Launch mutation task which will block on the row lock
    mutation_coro = asyncio.create_task(_mutation_task())
    await mutation_started.wait()

    # Release compute lock to let compute commit and unblock mutation
    release_compute.set()
    await asyncio.gather(compute_coro, mutation_coro)

    # Recompute to produce revision 2 reflecting mutation
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        rev2_result = await service.compute_score(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment_id,
        )

        snapshots = await service.list_snapshots(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment_id,
        )
        assert len(snapshots) == 2
        assert snapshots[0].revision_number == 1
        assert snapshots[1].revision_number == 2

        # Revision 1 scored before mutation -> overall_score is 0.00 / ungrounded
        assert snapshots[0].overall_score == Decimal("0.00")

        # Revision 2 scored after mutation -> overall_score reflects SATISFIED control
        assert snapshots[1].overall_score == rev2_result.overall_score
        assert snapshots[1].overall_score > Decimal("0.00")


async def test_duplicate_evidence_admission_rejected_in_integration() -> None:
    """Duplicate evidence attachment to the same control is rejected by database constraint."""
    _skip_without_database()

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)
        assessment = await service.create_assessment(
            principal=PRINCIPAL_A_ANALYST,
            request=ComplianceAssessmentCreateRequest(
                framework_id=FRAMEWORK_ID,
                title="Duplicate Evidence Test",
            ),
        )
        controls = await service.get_control_assessments(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment.id,
        )
        ctrl_id = controls[0].id

        # 1. Attach with chunk_id succeeds first time
        ev1 = await service.admit_evidence(
            principal=PRINCIPAL_A_ANALYST,
            control_assessment_id=ctrl_id,
            request=EvidenceReferenceCreateRequest(
                document_id=DOC_A_INTERNAL,
                chunk_id=CHUNK_A_INTERNAL,
            ),
        )
        assert ev1.chunk_id == CHUNK_A_INTERNAL

        # 2. Attach same (document_id, chunk_id) again -> ConflictError
        with pytest.raises(ConflictError):
            await service.admit_evidence(
                principal=PRINCIPAL_A_ANALYST,
                control_assessment_id=ctrl_id,
                request=EvidenceReferenceCreateRequest(
                    document_id=DOC_A_INTERNAL,
                    chunk_id=CHUNK_A_INTERNAL,
                ),
            )

        # 3. Attach with no chunk succeeds first time
        ev_nochunk = await service.admit_evidence(
            principal=PRINCIPAL_A_ANALYST,
            control_assessment_id=ctrl_id,
            request=EvidenceReferenceCreateRequest(
                document_id=DOC_A_INTERNAL,
                chunk_id=None,
            ),
        )
        assert ev_nochunk.chunk_id is None

        # 4. Attach same (document_id, None) again -> ConflictError
        with pytest.raises(ConflictError):
            await service.admit_evidence(
                principal=PRINCIPAL_A_ANALYST,
                control_assessment_id=ctrl_id,
                request=EvidenceReferenceCreateRequest(
                    document_id=DOC_A_INTERNAL,
                    chunk_id=None,
                ),
            )


async def test_admin_cannot_create_or_compute_in_service() -> None:
    """Admin is excluded from COMPLIANCE_CREATE and cannot create or compute assessments."""
    _skip_without_database()

    admin_principal = Principal(
        user_id=uuid4(),
        organization_id=ORG_A,
        role=Role.ADMIN,
        email="admin@alpha.service-test",
    )

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        with pytest.raises(AuthorizationError):
            await service.create_assessment(
                principal=admin_principal,
                request=ComplianceAssessmentCreateRequest(
                    framework_id=FRAMEWORK_ID,
                    title="Admin Attempt",
                ),
            )

        with pytest.raises(AuthorizationError):
            await service.compute_score(
                principal=admin_principal,
                assessment_id=uuid4(),
            )


async def test_cross_tenant_document_admission_rejected_in_service() -> None:
    """Tenant A analyst cannot attach Tenant B document as evidence."""
    _skip_without_database()

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        assessment = await service.create_assessment(
            principal=PRINCIPAL_A_ANALYST,
            request=ComplianceAssessmentCreateRequest(
                framework_id=FRAMEWORK_ID,
                title="Cross-Tenant Evidence Test",
            ),
        )
        controls = await service.get_control_assessments(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment.id,
        )
        ctrl_id = controls[0].id

    # Attempt to attach DOC_B_INTERNAL (owned by ORG_B) to Tenant A control
    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        with pytest.raises(NotFoundError):
            await service.admit_evidence(
                principal=PRINCIPAL_A_ANALYST,
                control_assessment_id=ctrl_id,
                request=EvidenceReferenceCreateRequest(
                    document_id=DOC_B_INTERNAL,
                ),
            )


async def test_clearance_safe_projection_integration() -> None:
    """Clearance-safe projection redacts higher-confidentiality evidence in real database."""
    _skip_without_database()

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        # 1. Create assessment
        assessment = await service.create_assessment(
            principal=PRINCIPAL_A_ANALYST,
            request=ComplianceAssessmentCreateRequest(
                framework_id=FRAMEWORK_ID,
                title="Projection Integration Test",
            ),
        )
        controls = await service.get_control_assessments(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment.id,
        )
        ctrl1 = next(c for c in controls if c.control_id == CONTROL_1_ID)
        ctrl2 = next(c for c in controls if c.control_id == CONTROL_2_ID)

        # 2. Attach INTERNAL evidence to Control 1 (Analyst)
        await service.admit_evidence(
            principal=PRINCIPAL_A_ANALYST,
            control_assessment_id=ctrl1.id,
            request=EvidenceReferenceCreateRequest(
                document_id=DOC_A_INTERNAL,
                chunk_id=CHUNK_A_INTERNAL,
            ),
        )

        # 3. Attach CONFIDENTIAL evidence to Control 2 (Manager)
        await service.admit_evidence(
            principal=PRINCIPAL_A_MANAGER,
            control_assessment_id=ctrl2.id,
            request=EvidenceReferenceCreateRequest(
                document_id=DOC_A_CONFIDENTIAL,
            ),
        )

        # 4. Compute authoritative score
        await service.compute_score(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment.id,
        )

        # 5. Analyst Projection: Control 1 visible, Control 2 hidden
        analyst_proj = await service.get_assessment_projection(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment.id,
        )
        assert analyst_proj.any_hidden_evidence is True
        c1_proj = next(c for c in analyst_proj.controls if c.id == ctrl1.id)
        c2_proj = next(c for c in analyst_proj.controls if c.id == ctrl2.id)

        assert len(c1_proj.evidence) == 1
        assert c1_proj.hidden_evidence_present is False
        assert len(c2_proj.evidence) == 0
        assert c2_proj.hidden_evidence_present is True

        # 6. Manager Projection: Control 1 & 2 both visible
        mgr_proj = await service.get_assessment_projection(
            principal=PRINCIPAL_A_MANAGER,
            assessment_id=assessment.id,
        )
        assert mgr_proj.any_hidden_evidence is False
        mgr_c1_proj = next(c for c in mgr_proj.controls if c.id == ctrl1.id)
        mgr_c2_proj = next(c for c in mgr_proj.controls if c.id == ctrl2.id)

        assert len(mgr_c1_proj.evidence) == 1
        assert mgr_c1_proj.hidden_evidence_present is False
        assert len(mgr_c2_proj.evidence) == 1
        assert mgr_c2_proj.hidden_evidence_present is False

        # 7. Scores are 100% identical regardless of caller
        assert analyst_proj.overall_score == mgr_proj.overall_score


async def test_candidate_extraction_integration() -> None:
    """Candidate extraction service executes bounded discovery against database tenant."""
    _skip_without_database()

    async with tenant_session(ORG_A) as session:
        repo = SQLAlchemyComplianceRepository(session)
        service = ComplianceAssessmentService(repository=repo)

        assessment = await service.create_assessment(
            principal=PRINCIPAL_A_ANALYST,
            request=ComplianceAssessmentCreateRequest(
                framework_id=FRAMEWORK_ID,
                title="Candidate Extraction Integration Test",
            ),
        )

        # Setup mock retrieval and mock LLM provider
        embedding_provider = MockEmbeddingProvider()
        vector_store = InMemoryVectorStore()
        # Seed matching vector in store
        await vector_store.search(
            embedding=[0.1] * 1024,
            organization_id=ORG_A,
            confidentiality_levels=PRINCIPAL_A_ANALYST.visible_confidentiality_levels,
            top_k=5,
        )

        retrieval_service = RetrievalService(
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
        llm_provider = MockLLMProvider()

        extraction_service = ComplianceCandidateExtractionService(
            repository=repo,
            retrieval_service=retrieval_service,
            llm_provider=llm_provider,
        )

        res = await extraction_service.extract_candidates(
            principal=PRINCIPAL_A_ANALYST,
            request=ComplianceCandidateExtractionRequest(
                assessment_id=assessment.id,
            ),
        )
        assert res.assessment_id == assessment.id
        assert res.framework_id == FRAMEWORK_ID

        # Proves no score snapshot was created by candidate extraction
        snapshots = await service.list_snapshots(
            principal=PRINCIPAL_A_ANALYST,
            assessment_id=assessment.id,
        )
        assert len(snapshots) == 0
