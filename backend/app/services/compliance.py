"""Application service for compliance assessment orchestration and deterministic scoring."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.compliance import (
    AssessmentScoreResult,
    AssessmentScoreSnapshotProjection,
    AssessmentScoringInput,
    ComplianceAssessmentCreateRequest,
    ComplianceAssessmentProjection,
    ComplianceAssessmentResponse,
    ControlAssessmentProjection,
    ControlAssessmentResponse,
    ControlAssessmentUpdateRequest,
    ControlScoringInput,
    EvidenceReferenceCreateRequest,
    EvidenceReferenceResponse,
    ScoreOverrideCreateRequest,
    ScoreOverrideResponse,
    VisibleEvidenceReference,
)
from app.models.enums import AssessmentStatus, RiskClassification
from app.models.principal import Principal
from app.ports.compliance_repository import ComplianceRepository
from app.security.authz import Permission, require_permission
from app.services.compliance_scoring import RiskScoringEngine

logger = get_logger(__name__)


class ComplianceAssessmentService:
    """Coordinates compliance assessments, evidence admission, and deterministic scoring.

    Security and Architectural Invariants:
    1. Tenant Isolation: ``organization_id`` is always taken directly from the verified
       ``Principal``; foreign tenant data is inaccessible and returns ``NotFoundError``
       to prevent resource enumeration.
    2. Segregation of Duties: ``Permission.COMPLIANCE_CREATE`` is required for assessment
       creation, control updates, evidence admission, and score computation (granted to
       Analyst and Manager; denied to Admin).
    3. Evidence Admission & Provenance: Document and chunk existence, tenant ownership, and
       caller clearance ceiling (``principal.can_read(...)``) are strictly verified
       before admission. Confidentiality levels and evidence snippets are derived solely
       from trusted persistence (client-provided snippets are rejected/ignored).
    4. Authoritative Scoring: Pure mathematical function over persisted inputs;
       caller clearance never alters the scoring computation.
    5. Single Assessment Lock Discipline: All scoring and scoring-input mutations
       (control updates, evidence admission, compute_score) acquire the parent
       assessment row FOR UPDATE before reading or modifying scoring inputs, guaranteeing
       serialized execution and coherent scoring snapshots.
    6. Event Publication: Durable transaction-coupled event publication is deferred
       until an outbox/after-commit mechanism is introduced.
    """

    def __init__(
        self,
        *,
        repository: ComplianceRepository,
        scoring_engine: RiskScoringEngine | None = None,
    ) -> None:
        self._repo = repository
        self._engine = scoring_engine or RiskScoringEngine()

    async def create_assessment(
        self,
        *,
        principal: Principal,
        request: ComplianceAssessmentCreateRequest,
    ) -> ComplianceAssessmentResponse:
        """Atomically initialize a compliance assessment with default control rows."""
        require_permission(principal, Permission.COMPLIANCE_CREATE)

        framework = await self._repo.get_framework(request.framework_id)
        if framework is None:
            raise NotFoundError("The requested compliance framework does not exist.")

        controls = await self._repo.get_framework_controls(request.framework_id)
        if not controls:
            raise ValidationError(
                "Cannot initialize an assessment for a framework with no controls defined."
            )

        title = request.title.strip()
        if not title:
            raise ValidationError("Assessment title cannot be empty.")

        assessment = await self._repo.create_assessment(
            organization_id=principal.organization_id,
            framework_id=request.framework_id,
            title=title,
            created_by=principal.user_id,
            controls=controls,
        )

        logger.info(
            "compliance_assessment_created",
            organization_id=str(principal.organization_id),
            assessment_id=str(assessment.id),
            framework_id=str(framework.id),
            created_by=str(principal.user_id),
        )

        return assessment

    async def _get_active_override_and_effective_scores(
        self,
        *,
        organization_id: UUID,
        assessment: ComplianceAssessmentResponse,
    ) -> tuple[ScoreOverrideResponse | None, Decimal | None, RiskClassification]:
        """Compute active override and effective scores for an assessment.

        An override is ONLY active/effective if:
        1. The assessment currently has a valid deterministic computation
           (scoring_version is not None).
        2. The latest snapshot matches the current materialized assessment computation.
        3. The latest override references that exact latest snapshot
           (matching snapshot_id and source_revision_number).

        Otherwise, active override is None and effective scores equal the assessment's
        current scores.
        """
        if assessment.scoring_version is None:
            return (None, assessment.overall_score, assessment.risk_classification)

        latest_snapshot = await self._repo.get_latest_snapshot(
            organization_id=organization_id,
            assessment_id=assessment.id,
        )
        if latest_snapshot is None:
            return (None, assessment.overall_score, assessment.risk_classification)

        if (
            assessment.scoring_version != latest_snapshot.scoring_version
            or assessment.overall_score != latest_snapshot.overall_score
            or assessment.risk_classification != latest_snapshot.risk_classification
        ):
            return (None, assessment.overall_score, assessment.risk_classification)

        latest_override = await self._repo.get_latest_score_override(
            organization_id=organization_id,
            assessment_id=assessment.id,
        )
        if latest_override is None:
            return (None, assessment.overall_score, assessment.risk_classification)

        if (
            latest_override.snapshot_id != latest_snapshot.id
            or latest_override.source_revision_number != latest_snapshot.revision_number
        ):
            return (None, assessment.overall_score, assessment.risk_classification)

        return (
            latest_override,
            latest_override.override_overall_score,
            latest_override.override_risk_classification,
        )

    async def get_assessment(
        self,
        *,
        principal: Principal,
        assessment_id: UUID,
    ) -> ComplianceAssessmentResponse:
        """Fetch one compliance assessment within the tenant boundary."""
        require_permission(principal, Permission.RISK_READ)

        assessment = await self._repo.get_assessment(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        if assessment is None:
            raise NotFoundError("The requested compliance assessment does not exist.")

        active_override, effective_score, effective_cls = (
            await self._get_active_override_and_effective_scores(
                organization_id=principal.organization_id,
                assessment=assessment,
            )
        )

        return ComplianceAssessmentResponse(
            id=assessment.id,
            organization_id=assessment.organization_id,
            framework_id=assessment.framework_id,
            title=assessment.title,
            status=assessment.status,
            overall_score=assessment.overall_score,
            risk_classification=assessment.risk_classification,
            scoring_version=assessment.scoring_version,
            created_by=assessment.created_by,
            created_at=assessment.created_at,
            updated_at=assessment.updated_at,
            effective_overall_score=effective_score,
            effective_risk_classification=effective_cls,
            latest_override=active_override,
        )

    async def list_assessments(
        self,
        *,
        principal: Principal,
        limit: int = 25,
        offset: int = 0,
    ) -> list[ComplianceAssessmentResponse]:
        """List compliance assessments for the caller's tenant."""
        require_permission(principal, Permission.RISK_READ)
        assessments = await self._repo.list_assessments(
            organization_id=principal.organization_id,
            limit=limit,
            offset=offset,
        )
        results: list[ComplianceAssessmentResponse] = []
        for a in assessments:
            active_override, effective_score, effective_cls = (
                await self._get_active_override_and_effective_scores(
                    organization_id=principal.organization_id,
                    assessment=a,
                )
            )
            results.append(
                ComplianceAssessmentResponse(
                    id=a.id,
                    organization_id=a.organization_id,
                    framework_id=a.framework_id,
                    title=a.title,
                    status=a.status,
                    overall_score=a.overall_score,
                    risk_classification=a.risk_classification,
                    scoring_version=a.scoring_version,
                    created_by=a.created_by,
                    created_at=a.created_at,
                    updated_at=a.updated_at,
                    effective_overall_score=effective_score,
                    effective_risk_classification=effective_cls,
                    latest_override=active_override,
                )
            )
        return results

    async def get_control_assessments(
        self,
        *,
        principal: Principal,
        assessment_id: UUID,
    ) -> list[ControlAssessmentResponse]:
        """Fetch all control assessments for a given assessment."""
        require_permission(principal, Permission.RISK_READ)

        # Enforce assessment existence and tenant isolation
        await self.get_assessment(principal=principal, assessment_id=assessment_id)

        return await self._repo.get_control_assessments(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )

    async def update_control_assessment(
        self,
        *,
        principal: Principal,
        control_assessment_id: UUID,
        request: ControlAssessmentUpdateRequest,
    ) -> ControlAssessmentResponse:
        """Update status, effective weight, or rationale for a control assessment."""
        require_permission(principal, Permission.COMPLIANCE_CREATE)

        ca = await self._repo.get_control_assessment(
            organization_id=principal.organization_id,
            control_assessment_id=control_assessment_id,
        )
        if ca is None:
            raise NotFoundError("The requested control assessment does not exist.")

        # Acquire parent assessment lock FOR UPDATE before mutating
        assessment = await self._repo.lock_assessment(
            organization_id=principal.organization_id,
            assessment_id=ca.assessment_id,
        )
        if assessment is None:
            raise NotFoundError("The parent compliance assessment does not exist.")

        if assessment.status in (AssessmentStatus.COMPLETED, AssessmentStatus.ARCHIVED):
            raise ConflictError(
                "Completed or archived compliance assessments cannot be modified."
            )

        if request.effective_weight is not None:
            weight = request.effective_weight
            if weight.is_nan() or weight.is_infinite():
                raise ValidationError("effective_weight must be a finite decimal.")
            if weight < Decimal("1.0") or weight > Decimal("5.0"):
                raise ValidationError("effective_weight must be between 1.0 and 5.0.")

        updated = await self._repo.update_control_assessment(
            organization_id=principal.organization_id,
            control_assessment_id=control_assessment_id,
            status=request.status,
            effective_weight=request.effective_weight,
            rationale=request.rationale,
            touch_assessment=True,
        )
        if updated is None:
            raise NotFoundError("The requested control assessment does not exist.")

        logger.info(
            "control_assessment_updated",
            organization_id=str(principal.organization_id),
            control_assessment_id=str(control_assessment_id),
            user_id=str(principal.user_id),
        )

        return updated

    async def admit_evidence(
        self,
        *,
        principal: Principal,
        control_assessment_id: UUID,
        request: EvidenceReferenceCreateRequest,
    ) -> EvidenceReferenceResponse:
        """Admit a validated document or chunk reference as evidence for a control."""
        require_permission(principal, Permission.COMPLIANCE_CREATE)

        ca = await self._repo.get_control_assessment(
            organization_id=principal.organization_id,
            control_assessment_id=control_assessment_id,
        )
        if ca is None:
            raise NotFoundError("The requested control assessment does not exist.")

        # Acquire parent assessment lock FOR UPDATE before inserting evidence
        assessment = await self._repo.lock_assessment(
            organization_id=principal.organization_id,
            assessment_id=ca.assessment_id,
        )
        if assessment is None:
            raise NotFoundError("The parent compliance assessment does not exist.")

        if assessment.status in (AssessmentStatus.COMPLETED, AssessmentStatus.ARCHIVED):
            raise ConflictError(
                "Evidence cannot be added to completed or archived compliance assessments."
            )

        # Validate tenant document ownership and extract trusted confidentiality
        doc_info = await self._repo.get_document_for_evidence(
            organization_id=principal.organization_id,
            document_id=request.document_id,
        )
        if doc_info is None:
            raise NotFoundError("The referenced document does not exist.")

        _, doc_confidentiality = doc_info

        # Enforce caller clearance ceiling at admission time
        if not principal.can_read(doc_confidentiality):
            raise AuthorizationError(
                "You do not have permission to attach evidence with this confidentiality level."
            )

        snippet: str | None = None
        if request.chunk_id is not None:
            chunk_info = await self._repo.get_chunk_for_evidence(
                organization_id=principal.organization_id,
                chunk_id=request.chunk_id,
            )
            if chunk_info is None or chunk_info[1] != request.document_id:
                raise ValidationError(
                    "The referenced document chunk does not exist or "
                    "does not belong to the specified document."
                )
            snippet = chunk_info[2]

        evidence = await self._repo.add_evidence_reference(
            organization_id=principal.organization_id,
            control_assessment_id=control_assessment_id,
            document_id=request.document_id,
            chunk_id=request.chunk_id,
            confidentiality_level=doc_confidentiality,
            snippet=snippet,
            created_by=principal.user_id,
            touch_assessment=True,
        )

        logger.info(
            "compliance_evidence_admitted",
            organization_id=str(principal.organization_id),
            control_assessment_id=str(control_assessment_id),
            document_id=str(request.document_id),
            chunk_id=str(request.chunk_id) if request.chunk_id else None,
            confidentiality=doc_confidentiality.value,
            admitted_by=str(principal.user_id),
        )

        return evidence

    async def compute_score(
        self,
        *,
        principal: Principal,
        assessment_id: UUID,
    ) -> AssessmentScoreResult:
        """Deterministically compute compliance score and persist immutable audit snapshot.

        Enforces strict single assessment-level serialization discipline:
        1. Lock tenant assessment row FOR UPDATE.
        2. Read framework, control assessments, and admitted evidence references.
        3. Build AssessmentScoringInput and compute deterministic RiskScoringEngine result.
        4. Allocate next revision number, insert snapshot, and update assessment scores.
        """
        require_permission(principal, Permission.COMPLIANCE_CREATE)

        # 1. Lock tenant assessment row FOR UPDATE at start of compute transaction
        assessment = await self._repo.lock_assessment(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        if assessment is None:
            raise NotFoundError("The requested compliance assessment does not exist.")

        if assessment.status in (AssessmentStatus.COMPLETED, AssessmentStatus.ARCHIVED):
            raise ConflictError(
                "Completed or archived compliance assessments cannot be recomputed."
            )

        # 2. Read framework
        framework = await self._repo.get_framework(assessment.framework_id)
        if framework is None:
            raise NotFoundError(
                "The compliance framework for this assessment does not exist."
            )

        # 3. Read control assessments and evidence references
        control_assessments = await self._repo.get_control_assessments(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        evidence_refs = await self._repo.get_evidence_references_for_assessment(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )

        # Group evidence reference IDs by control assessment ID
        evidence_by_control: dict[UUID, list[UUID]] = {ca.id: [] for ca in control_assessments}
        for ev in evidence_refs:
            if ev.control_assessment_id in evidence_by_control:
                evidence_by_control[ev.control_assessment_id].append(ev.id)

        # Deterministically order control assessments by control_id
        sorted_control_assessments = sorted(
            control_assessments, key=lambda ca: str(ca.control_id)
        )

        # Build pure mathematical inputs for the scoring engine
        controls_input = [
            ControlScoringInput(
                control_id=str(ca.control_id),
                status=ca.status,
                effective_weight=ca.effective_weight,
                evidence_count=len(evidence_by_control.get(ca.id, [])),
            )
            for ca in sorted_control_assessments
        ]

        scoring_input = AssessmentScoringInput(
            framework_id=str(framework.id),
            framework_version=framework.version,
            controls=controls_input,
            scoring_version="v1.0",
        )

        # Execute pure deterministic calculation
        score_result = self._engine.compute(scoring_input)

        # Build canonical audit input snapshot with exact validated evidence IDs
        # Persist identifiers and scoring parameters only — no evidence snippets or raw content
        audit_controls_snapshot = [
            {
                "control_id": str(ca.control_id),
                "status": ca.status.value,
                "effective_weight": str(ca.effective_weight),
                "evidence_count": len(evidence_by_control.get(ca.id, [])),
                "evidence_reference_ids": sorted(
                    str(ev_id) for ev_id in evidence_by_control.get(ca.id, [])
                ),
            }
            for ca in sorted_control_assessments
        ]

        audit_input_snapshot = {
            "framework_id": str(framework.id),
            "framework_version": framework.version,
            "scoring_version": score_result.scoring_version,
            "controls": audit_controls_snapshot,
        }

        # Persist snapshot and update assessment scores atomically
        raw_scores_payload = {k: str(v) for k, v in score_result.raw_scores.items()}
        snapshot = await self._repo.save_score_snapshot_and_update_assessment(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
            scoring_version=score_result.scoring_version,
            framework_version=score_result.framework_version,
            input_snapshot=audit_input_snapshot,
            raw_scores=raw_scores_payload,
            overall_score=score_result.overall_score,
            risk_classification=score_result.risk_classification,
            computed_by=principal.user_id,
        )

        logger.info(
            "compliance_assessment_computed",
            organization_id=str(principal.organization_id),
            assessment_id=str(assessment_id),
            revision_number=snapshot.revision_number,
            overall_score=(
                str(score_result.overall_score)
                if score_result.overall_score is not None
                else None
            ),
            risk_classification=score_result.risk_classification.value,
            computed_by=str(principal.user_id),
        )

        return score_result

    async def list_snapshots(
        self,
        *,
        principal: Principal,
        assessment_id: UUID,
    ) -> list[AssessmentScoreSnapshotProjection]:
        """List clearance-safe historical score snapshots for an assessment."""
        require_permission(principal, Permission.RISK_READ)

        # Enforce assessment existence and tenant isolation
        await self.get_assessment(principal=principal, assessment_id=assessment_id)

        snapshots = await self._repo.list_snapshots(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        return [
            AssessmentScoreSnapshotProjection(
                id=s.id,
                organization_id=s.organization_id,
                assessment_id=s.assessment_id,
                revision_number=s.revision_number,
                scoring_version=s.scoring_version,
                framework_version=s.framework_version,
                raw_scores=s.raw_scores,
                overall_score=s.overall_score,
                risk_classification=s.risk_classification,
                computed_by=s.computed_by,
                computed_at=s.computed_at,
            )
            for s in snapshots
        ]

    async def get_assessment_projection(
        self,
        *,
        principal: Principal,
        assessment_id: UUID,
    ) -> ComplianceAssessmentProjection:
        """Fetch a clearance-safe projection of a compliance assessment.

        Preserves authoritative mathematical scores (which are actor-independent) while
        redacting any evidence that exceeds the caller's clearance ceiling. If any
        evidence is omitted, ``hidden_evidence_present`` is set to True on that control,
        without leaking any metadata, IDs, counts, or snippets of the hidden evidence.
        """
        require_permission(principal, Permission.RISK_READ)

        assessment = await self.get_assessment(
            principal=principal, assessment_id=assessment_id
        )
        control_assessments = await self._repo.get_control_assessments(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        evidence_refs = await self._repo.get_evidence_references_for_assessment(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )

        evidence_by_control: dict[UUID, list[EvidenceReferenceResponse]] = {
            ca.id: [] for ca in control_assessments
        }
        for ev in evidence_refs:
            if ev.control_assessment_id in evidence_by_control:
                evidence_by_control[ev.control_assessment_id].append(ev)

        control_projections: list[ControlAssessmentProjection] = []
        for ca in control_assessments:
            all_evs = evidence_by_control.get(ca.id, [])
            visible_evs: list[VisibleEvidenceReference] = []
            hidden_count = 0
            for ev in all_evs:
                if principal.can_read(ev.confidentiality_level):
                    visible_evs.append(
                        VisibleEvidenceReference(
                            id=ev.id,
                            control_assessment_id=ev.control_assessment_id,
                            document_id=ev.document_id,
                            chunk_id=ev.chunk_id,
                            confidentiality_level=ev.confidentiality_level,
                            snippet=ev.snippet,
                            created_by=ev.created_by,
                            created_at=ev.created_at,
                        )
                    )
                else:
                    hidden_count += 1

            control_projections.append(
                ControlAssessmentProjection(
                    id=ca.id,
                    organization_id=ca.organization_id,
                    assessment_id=ca.assessment_id,
                    control_id=ca.control_id,
                    status=ca.status,
                    effective_weight=ca.effective_weight,
                    rationale=ca.rationale,
                    created_at=ca.created_at,
                    updated_at=ca.updated_at,
                    evidence=visible_evs,
                    hidden_evidence_present=hidden_count > 0,
                )
            )

        active_override, effective_score, effective_cls = (
            await self._get_active_override_and_effective_scores(
                organization_id=principal.organization_id,
                assessment=assessment,
            )
        )

        return ComplianceAssessmentProjection(
            id=assessment.id,
            organization_id=assessment.organization_id,
            framework_id=assessment.framework_id,
            title=assessment.title,
            status=assessment.status,
            overall_score=assessment.overall_score,
            risk_classification=assessment.risk_classification,
            scoring_version=assessment.scoring_version,
            created_by=assessment.created_by,
            created_at=assessment.created_at,
            updated_at=assessment.updated_at,
            effective_overall_score=effective_score,
            effective_risk_classification=effective_cls,
            latest_override=active_override,
            controls=control_projections,
            any_hidden_evidence=any(c.hidden_evidence_present for c in control_projections),
        )

    async def create_score_override(
        self,
        *,
        principal: Principal,
        assessment_id: UUID,
        request: ScoreOverrideCreateRequest,
    ) -> ScoreOverrideResponse:
        """Persist an immutable human risk score override for an assessment.

        Requires Permission.RISK_MODIFY_SEVERITY (Manager only).
        Locks the assessment row FOR UPDATE to ensure consistency with snapshots.
        """
        require_permission(principal, Permission.RISK_MODIFY_SEVERITY)

        # 1. Lock parent assessment FOR UPDATE
        assessment = await self._repo.lock_assessment(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        if assessment is None:
            raise NotFoundError("The requested compliance assessment does not exist.")

        if assessment.status == AssessmentStatus.ARCHIVED:
            raise ConflictError("Archived compliance assessments cannot be overridden.")

        # 2. Fetch latest snapshot being overridden
        latest_snapshot = await self._repo.get_latest_snapshot(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        if latest_snapshot is None:
            raise ConflictError(
                "Cannot override score before at least one score computation snapshot exists."
            )

        # 3. Enforce current computation validity and freshness
        if (
            assessment.scoring_version is None
            or assessment.scoring_version != latest_snapshot.scoring_version
            or assessment.overall_score != latest_snapshot.overall_score
            or assessment.risk_classification != latest_snapshot.risk_classification
        ):
            raise ConflictError(
                "Cannot override score when assessment scoring inputs have been modified "
                "since last computation. Recompute score first."
            )

        if (
            latest_snapshot.overall_score is None
            or latest_snapshot.risk_classification == RiskClassification.NOT_SCORED
        ):
            raise ConflictError(
                "Cannot override score for an assessment with zero applicable controls "
                "(status is NOT_SCORED)."
            )

        # 4. Normalize score and deterministically derive risk classification
        norm_score = request.override_overall_score.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        derived_classification = RiskScoringEngine.classify_score(norm_score)

        justification = request.justification.strip()
        if not justification:
            raise ValidationError("Override justification cannot be empty.")

        # 5. Save immutable score override
        override = await self._repo.create_score_override(
            organization_id=principal.organization_id,
            assessment_id=assessment.id,
            snapshot_id=latest_snapshot.id,
            source_revision_number=latest_snapshot.revision_number,
            original_overall_score=latest_snapshot.overall_score,
            original_risk_classification=latest_snapshot.risk_classification,
            override_overall_score=norm_score,
            override_risk_classification=derived_classification,
            justification=justification,
            overridden_by=principal.user_id,
        )

        logger.info(
            "compliance_score_overridden",
            organization_id=str(principal.organization_id),
            assessment_id=str(assessment_id),
            snapshot_id=str(latest_snapshot.id),
            source_revision_number=latest_snapshot.revision_number,
            override_overall_score=str(norm_score),
            override_risk_classification=derived_classification.value,
            overridden_by=str(principal.user_id),
        )

        return override

    async def finalize_assessment(
        self,
        *,
        principal: Principal,
        assessment_id: UUID,
    ) -> ComplianceAssessmentResponse:
        """Finalize an assessment to COMPLETED status after review.

        Requires Permission.RISK_REVIEW (Manager only).
        Locks the assessment row FOR UPDATE.
        """
        require_permission(principal, Permission.RISK_REVIEW)

        # 1. Lock parent assessment FOR UPDATE
        assessment = await self._repo.lock_assessment(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        if assessment is None:
            raise NotFoundError("The requested compliance assessment does not exist.")

        if assessment.status in (AssessmentStatus.COMPLETED, AssessmentStatus.ARCHIVED):
            raise ConflictError("Assessment is already completed or archived.")

        # 2. Enforce at least one computed score snapshot exists
        latest_snapshot = await self._repo.get_latest_snapshot(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        if latest_snapshot is None:
            raise ConflictError(
                "Cannot finalize an assessment before at least one score computation "
                "snapshot has been recorded."
            )

        # 3. Enforce current computation validity and freshness
        if (
            assessment.scoring_version is None
            or assessment.scoring_version != latest_snapshot.scoring_version
            or assessment.overall_score != latest_snapshot.overall_score
            or assessment.risk_classification != latest_snapshot.risk_classification
        ):
            raise ConflictError(
                "Assessment scoring inputs have been modified since the last computation. "
                "Recompute score before finalization."
            )

        finalized = await self._repo.finalize_assessment(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )
        if finalized is None:
            raise NotFoundError("The requested compliance assessment does not exist.")

        logger.info(
            "compliance_assessment_finalized",
            organization_id=str(principal.organization_id),
            assessment_id=str(assessment_id),
            user_id=str(principal.user_id),
        )

        active_override, effective_score, effective_cls = (
            await self._get_active_override_and_effective_scores(
                organization_id=principal.organization_id,
                assessment=finalized,
            )
        )

        return ComplianceAssessmentResponse(
            id=finalized.id,
            organization_id=finalized.organization_id,
            framework_id=finalized.framework_id,
            title=finalized.title,
            status=finalized.status,
            overall_score=finalized.overall_score,
            risk_classification=finalized.risk_classification,
            scoring_version=finalized.scoring_version,
            created_by=finalized.created_by,
            created_at=finalized.created_at,
            updated_at=finalized.updated_at,
            effective_overall_score=effective_score,
            effective_risk_classification=effective_cls,
            latest_override=active_override,
        )

    async def list_score_overrides(
        self,
        *,
        principal: Principal,
        assessment_id: UUID,
    ) -> list[ScoreOverrideResponse]:
        """List historical score overrides for an assessment in ascending chronological order."""
        require_permission(principal, Permission.RISK_READ)

        # Enforce assessment existence and tenant isolation
        await self.get_assessment(principal=principal, assessment_id=assessment_id)

        return await self._repo.list_score_overrides(
            organization_id=principal.organization_id,
            assessment_id=assessment_id,
        )

    async def get_control_assessment_projection(
        self,
        *,
        principal: Principal,
        control_assessment_id: UUID,
    ) -> ControlAssessmentProjection:
        """Fetch a clearance-safe projection for a single control assessment."""
        require_permission(principal, Permission.RISK_READ)

        ca = await self._repo.get_control_assessment(
            organization_id=principal.organization_id,
            control_assessment_id=control_assessment_id,
        )
        if ca is None:
            raise NotFoundError("The requested control assessment does not exist.")

        # Enforce parent assessment tenant existence
        await self.get_assessment(principal=principal, assessment_id=ca.assessment_id)

        evidence_refs = await self._repo.get_evidence_references_for_control(
            organization_id=principal.organization_id,
            control_assessment_id=control_assessment_id,
        )

        visible_evs: list[VisibleEvidenceReference] = []
        hidden_count = 0
        for ev in evidence_refs:
            if principal.can_read(ev.confidentiality_level):
                visible_evs.append(
                    VisibleEvidenceReference(
                        id=ev.id,
                        control_assessment_id=ev.control_assessment_id,
                        document_id=ev.document_id,
                        chunk_id=ev.chunk_id,
                        confidentiality_level=ev.confidentiality_level,
                        snippet=ev.snippet,
                        created_by=ev.created_by,
                        created_at=ev.created_at,
                    )
                )
            else:
                hidden_count += 1

        return ControlAssessmentProjection(
            id=ca.id,
            organization_id=ca.organization_id,
            assessment_id=ca.assessment_id,
            control_id=ca.control_id,
            status=ca.status,
            effective_weight=ca.effective_weight,
            rationale=ca.rationale,
            created_at=ca.created_at,
            updated_at=ca.updated_at,
            evidence=visible_evs,
            hidden_evidence_present=hidden_count > 0,
        )
