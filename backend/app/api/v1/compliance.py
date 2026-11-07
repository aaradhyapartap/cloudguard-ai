"""API routes for compliance assessments, deterministic scoring, overrides,
and candidate extraction.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.adapters.local.compliance_repository import SQLAlchemyComplianceRepository
from app.api.deps import ContainerDep, PrincipalDep, SessionDep, requires
from app.models.compliance import (
    AssessmentScoreResult,
    AssessmentScoreSnapshotProjection,
    ComplianceAssessmentCreateRequest,
    ComplianceAssessmentProjection,
    ComplianceAssessmentResponse,
    ComplianceCandidateExtractionRequest,
    ComplianceCandidateExtractionResult,
    ControlAssessmentProjection,
    ControlAssessmentResponse,
    ControlAssessmentUpdateRequest,
    EvidenceReferenceCreateRequest,
    EvidenceReferenceResponse,
    ScoreOverrideCreateRequest,
    ScoreOverrideResponse,
)
from app.security.authz import Permission
from app.services.compliance import ComplianceAssessmentService
from app.services.compliance_candidate_extraction import (
    ComplianceCandidateExtractionService,
)
from app.services.retrieval import RetrievalService

router = APIRouter(prefix="/compliance", tags=["compliance"])


def _service(session: SessionDep) -> ComplianceAssessmentService:
    repo = SQLAlchemyComplianceRepository(session)
    return ComplianceAssessmentService(repository=repo)


def _candidate_service(
    session: SessionDep, container: ContainerDep
) -> ComplianceCandidateExtractionService:
    repo = SQLAlchemyComplianceRepository(session)
    retrieval_service = RetrievalService(
        embedding_provider=container.embeddings,
        vector_store=container.vectors,
    )
    return ComplianceCandidateExtractionService(
        repository=repo,
        retrieval_service=retrieval_service,
        llm_provider=container.llm,
    )


@router.post(
    "/assessments",
    response_model=ComplianceAssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initialize a new compliance assessment",
    dependencies=[requires(Permission.COMPLIANCE_CREATE)],
)
async def create_assessment(
    payload: ComplianceAssessmentCreateRequest,
    session: SessionDep,
    principal: PrincipalDep,
) -> ComplianceAssessmentResponse:
    """Create a new compliance assessment with default unassessed control rows."""
    service = _service(session)
    return await service.create_assessment(principal=principal, request=payload)


@router.get(
    "/assessments",
    response_model=list[ComplianceAssessmentResponse],
    status_code=status.HTTP_200_OK,
    summary="List compliance assessments for the organization",
    dependencies=[requires(Permission.RISK_READ)],
)
async def list_assessments(
    session: SessionDep,
    principal: PrincipalDep,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ComplianceAssessmentResponse]:
    """List compliance assessments scoped to the caller's tenant."""
    service = _service(session)
    return await service.list_assessments(
        principal=principal,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/assessments/{assessment_id}",
    response_model=ComplianceAssessmentProjection,
    status_code=status.HTTP_200_OK,
    summary="Get a clearance-safe projection of an assessment",
    dependencies=[requires(Permission.RISK_READ)],
)
async def get_assessment(
    assessment_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> ComplianceAssessmentProjection:
    """Fetch assessment detail with evidence filtered to the caller's clearance ceiling."""
    service = _service(session)
    return await service.get_assessment_projection(
        principal=principal,
        assessment_id=assessment_id,
    )


@router.patch(
    "/control-assessments/{control_assessment_id}",
    response_model=ControlAssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update control assessment status, weight, or rationale",
    dependencies=[requires(Permission.COMPLIANCE_CREATE)],
)
async def update_control_assessment(
    control_assessment_id: UUID,
    payload: ControlAssessmentUpdateRequest,
    session: SessionDep,
    principal: PrincipalDep,
) -> ControlAssessmentResponse:
    """Update status, weight, or rationale on a single control assessment."""
    service = _service(session)
    return await service.update_control_assessment(
        principal=principal,
        control_assessment_id=control_assessment_id,
        request=payload,
    )


@router.get(
    "/control-assessments/{control_assessment_id}",
    response_model=ControlAssessmentProjection,
    status_code=status.HTTP_200_OK,
    summary="Get a clearance-safe projection of a control assessment",
    dependencies=[requires(Permission.RISK_READ)],
)
async def get_control_assessment(
    control_assessment_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> ControlAssessmentProjection:
    """Fetch a single control assessment with evidence filtered to caller clearance."""
    service = _service(session)
    return await service.get_control_assessment_projection(
        principal=principal,
        control_assessment_id=control_assessment_id,
    )


@router.post(
    "/control-assessments/{control_assessment_id}/evidence",
    response_model=EvidenceReferenceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admit a validated evidence reference to a control assessment",
    dependencies=[requires(Permission.COMPLIANCE_CREATE)],
)
async def admit_evidence(
    control_assessment_id: UUID,
    payload: EvidenceReferenceCreateRequest,
    session: SessionDep,
    principal: PrincipalDep,
) -> EvidenceReferenceResponse:
    """Admit evidence reference verifying tenant ownership and caller clearance."""
    service = _service(session)
    return await service.admit_evidence(
        principal=principal,
        control_assessment_id=control_assessment_id,
        request=payload,
    )


@router.post(
    "/assessments/{assessment_id}/compute",
    response_model=AssessmentScoreResult,
    status_code=status.HTTP_200_OK,
    summary="Deterministically compute score and record immutable snapshot",
    dependencies=[requires(Permission.COMPLIANCE_CREATE)],
)
async def compute_assessment_score(
    assessment_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> AssessmentScoreResult:
    """Compute score using pure mathematical engine and save immutable audit snapshot."""
    service = _service(session)
    return await service.compute_score(
        principal=principal,
        assessment_id=assessment_id,
    )


@router.post(
    "/assessments/{assessment_id}/candidates",
    response_model=ComplianceCandidateExtractionResult,
    status_code=status.HTTP_200_OK,
    summary="Extract non-authoritative candidate findings using bounded LLM analysis",
    dependencies=[requires(Permission.COMPLIANCE_CREATE)],
)
async def extract_candidate_findings(
    assessment_id: UUID,
    payload: ComplianceCandidateExtractionRequest,
    session: SessionDep,
    principal: PrincipalDep,
    container: ContainerDep,
) -> ComplianceCandidateExtractionResult:
    """Run bounded candidate extraction (proposals only; does not mutate scores or controls)."""
    service = _candidate_service(session, container)
    return await service.extract_candidates(
        principal=principal,
        request=payload,
    )


@router.post(
    "/assessments/{assessment_id}/finalize",
    response_model=ComplianceAssessmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Finalize compliance assessment after manager review",
    dependencies=[requires(Permission.RISK_REVIEW)],
)
async def finalize_assessment(
    assessment_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> ComplianceAssessmentResponse:
    """Finalize assessment to COMPLETED status (Manager only)."""
    service = _service(session)
    return await service.finalize_assessment(
        principal=principal,
        assessment_id=assessment_id,
    )


@router.post(
    "/assessments/{assessment_id}/override",
    response_model=ScoreOverrideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an immutable human risk score override",
    dependencies=[requires(Permission.RISK_MODIFY_SEVERITY)],
)
async def create_score_override(
    assessment_id: UUID,
    payload: ScoreOverrideCreateRequest,
    session: SessionDep,
    principal: PrincipalDep,
) -> ScoreOverrideResponse:
    """Override assessment score recording mandatory justification (Manager only)."""
    service = _service(session)
    return await service.create_score_override(
        principal=principal,
        assessment_id=assessment_id,
        request=payload,
    )


@router.get(
    "/assessments/{assessment_id}/snapshots",
    response_model=list[AssessmentScoreSnapshotProjection],
    status_code=status.HTTP_200_OK,
    summary="List immutable historical score snapshots",
    dependencies=[requires(Permission.RISK_READ)],
)
async def list_snapshots(
    assessment_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> list[AssessmentScoreSnapshotProjection]:
    """Fetch clearance-safe score computation snapshot projections for an assessment."""
    service = _service(session)
    return await service.list_snapshots(
        principal=principal,
        assessment_id=assessment_id,
    )


@router.get(
    "/assessments/{assessment_id}/overrides",
    response_model=list[ScoreOverrideResponse],
    status_code=status.HTTP_200_OK,
    summary="List immutable human score overrides",
    dependencies=[requires(Permission.RISK_READ)],
)
async def list_score_overrides(
    assessment_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> list[ScoreOverrideResponse]:
    """Fetch audit history of human score overrides for an assessment."""
    service = _service(session)
    return await service.list_score_overrides(
        principal=principal,
        assessment_id=assessment_id,
    )
