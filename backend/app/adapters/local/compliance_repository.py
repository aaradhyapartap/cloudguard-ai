"""SQLAlchemy repository implementation for compliance frameworks, assessments,
evidence, and scores.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.compliance import (
    AssessmentScoreSnapshotResponse,
    ComplianceAssessmentResponse,
    ComplianceControlRead,
    ComplianceFrameworkRead,
    ControlAssessmentResponse,
    EvidenceReferenceResponse,
    ScoreOverrideResponse,
)
from app.models.enums import (
    AssessmentStatus,
    ConfidentialityLevel,
    ControlStatus,
    RiskClassification,
)
from app.ports.compliance_repository import ComplianceRepository
from app.repositories.tables import (
    AssessmentScoreSnapshot,
    ComplianceAssessment,
    ComplianceControl,
    ComplianceFramework,
    ControlAssessment,
    Document,
    DocumentChunk,
    EvidenceReference,
    ScoreOverride,
)


class SQLAlchemyComplianceRepository(ComplianceRepository):
    """Local / CI repository backed by PostgreSQL through SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_framework(self, framework_id: UUID) -> ComplianceFrameworkRead | None:
        result = await self._session.execute(
            select(ComplianceFramework).where(ComplianceFramework.id == framework_id)
        )
        fw = result.scalar_one_or_none()
        if fw is None:
            return None
        return ComplianceFrameworkRead(
            id=fw.id,
            code=fw.code,
            name=fw.name,
            version=fw.version,
            description=fw.description,
            created_at=fw.created_at,
            updated_at=fw.updated_at,
        )

    async def get_framework_controls(
        self, framework_id: UUID
    ) -> list[ComplianceControlRead]:
        result = await self._session.execute(
            select(ComplianceControl)
            .where(ComplianceControl.framework_id == framework_id)
            .order_by(ComplianceControl.control_code.asc())
        )
        controls = result.scalars().all()
        return [
            ComplianceControlRead(
                id=c.id,
                framework_id=c.framework_id,
                control_code=c.control_code,
                title=c.title,
                description=c.description,
                category=c.category,
                default_weight=Decimal(str(c.default_weight)),
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in controls
        ]

    async def create_assessment(
        self,
        *,
        organization_id: UUID,
        framework_id: UUID,
        title: str,
        created_by: UUID,
        controls: list[ComplianceControlRead],
    ) -> ComplianceAssessmentResponse:
        assessment_id = uuid4()
        assessment = ComplianceAssessment(
            id=assessment_id,
            organization_id=organization_id,
            framework_id=framework_id,
            title=title,
            status=AssessmentStatus.DRAFT,
            overall_score=None,
            risk_classification=RiskClassification.NOT_SCORED,
            scoring_version=None,
            created_by=created_by,
        )
        self._session.add(assessment)

        control_entities = [
            ControlAssessment(
                id=uuid4(),
                organization_id=organization_id,
                assessment_id=assessment_id,
                control_id=ctrl.id,
                status=ControlStatus.UNASSESSED,
                effective_weight=ctrl.default_weight,
                rationale=None,
            )
            for ctrl in controls
        ]
        self._session.add_all(control_entities)
        await self._session.flush()
        await self._session.refresh(assessment)

        return self._to_assessment_response(assessment)

    async def get_assessment(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
        for_update: bool = False,
    ) -> ComplianceAssessmentResponse | None:
        stmt = select(ComplianceAssessment).where(
            ComplianceAssessment.organization_id == organization_id,
            ComplianceAssessment.id == assessment_id,
        )
        if for_update:
            stmt = stmt.with_for_update()

        result = await self._session.execute(stmt)
        assessment = result.scalar_one_or_none()
        if assessment is None:
            return None
        return self._to_assessment_response(assessment)

    async def lock_assessment(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> ComplianceAssessmentResponse | None:
        return await self.get_assessment(
            organization_id=organization_id,
            assessment_id=assessment_id,
            for_update=True,
        )

    async def list_assessments(
        self,
        *,
        organization_id: UUID,
        limit: int = 25,
        offset: int = 0,
    ) -> list[ComplianceAssessmentResponse]:
        result = await self._session.execute(
            select(ComplianceAssessment)
            .where(ComplianceAssessment.organization_id == organization_id)
            .order_by(ComplianceAssessment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        assessments = result.scalars().all()
        return [self._to_assessment_response(a) for a in assessments]

    async def get_control_assessment(
        self,
        *,
        organization_id: UUID,
        control_assessment_id: UUID,
    ) -> ControlAssessmentResponse | None:
        result = await self._session.execute(
            select(ControlAssessment).where(
                ControlAssessment.organization_id == organization_id,
                ControlAssessment.id == control_assessment_id,
            )
        )
        ca = result.scalar_one_or_none()
        if ca is None:
            return None

        ev_count_res = await self._session.execute(
            select(func.count())
            .select_from(EvidenceReference)
            .where(
                EvidenceReference.organization_id == organization_id,
                EvidenceReference.control_assessment_id == control_assessment_id,
            )
        )
        ev_count = int(ev_count_res.scalar_one())

        return self._to_control_response(ca, evidence_count=ev_count)

    async def get_control_assessments(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> list[ControlAssessmentResponse]:
        result = await self._session.execute(
            select(ControlAssessment)
            .where(
                ControlAssessment.organization_id == organization_id,
                ControlAssessment.assessment_id == assessment_id,
            )
            .order_by(ControlAssessment.created_at.asc())
        )
        cas = list(result.scalars().all())
        if not cas:
            return []

        # Aggregate evidence counts for all controls of this assessment
        ca_ids = [ca.id for ca in cas]
        ev_counts_res = await self._session.execute(
            select(
                EvidenceReference.control_assessment_id,
                func.count().label("ev_count"),
            )
            .where(
                EvidenceReference.organization_id == organization_id,
                EvidenceReference.control_assessment_id.in_(ca_ids),
            )
            .group_by(EvidenceReference.control_assessment_id)
        )
        counts_map = {row[0]: int(row[1]) for row in ev_counts_res.all()}

        return [
            self._to_control_response(
                ca, evidence_count=counts_map.get(ca.id, 0)
            )
            for ca in cas
        ]

    async def update_control_assessment(
        self,
        *,
        organization_id: UUID,
        control_assessment_id: UUID,
        status: ControlStatus | None = None,
        effective_weight: Decimal | None = None,
        rationale: str | None = None,
        touch_assessment: bool = True,
    ) -> ControlAssessmentResponse | None:
        result = await self._session.execute(
            select(ControlAssessment).where(
                ControlAssessment.organization_id == organization_id,
                ControlAssessment.id == control_assessment_id,
            )
        )
        ca = result.scalar_one_or_none()
        if ca is None:
            return None

        # Acquire parent assessment row lock FOR UPDATE before mutating
        parent_res = await self._session.execute(
            select(ComplianceAssessment)
            .where(
                ComplianceAssessment.organization_id == organization_id,
                ComplianceAssessment.id == ca.assessment_id,
            )
            .with_for_update()
        )
        parent = parent_res.scalar_one_or_none()

        if status is not None:
            ca.status = status
        if effective_weight is not None:
            ca.effective_weight = effective_weight
        if rationale is not None:
            ca.rationale = rationale

        if touch_assessment and parent is not None:
            if parent.status == AssessmentStatus.DRAFT:
                parent.status = AssessmentStatus.IN_PROGRESS
            # Invalidate current computed score
            parent.overall_score = None
            parent.risk_classification = RiskClassification.NOT_SCORED
            parent.scoring_version = None

        await self._session.flush()
        await self._session.refresh(ca)

        ev_count_res = await self._session.execute(
            select(func.count())
            .select_from(EvidenceReference)
            .where(
                EvidenceReference.organization_id == organization_id,
                EvidenceReference.control_assessment_id == control_assessment_id,
            )
        )
        ev_count = int(ev_count_res.scalar_one())

        return self._to_control_response(ca, evidence_count=ev_count)

    async def get_document_for_evidence(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
    ) -> tuple[UUID, ConfidentialityLevel] | None:
        result = await self._session.execute(
            select(Document.id, Document.confidentiality_level).where(
                Document.organization_id == organization_id,
                Document.id == document_id,
            )
        )
        row = result.first()
        if row is None:
            return None
        return (row[0], row[1])

    async def get_chunk_for_evidence(
        self,
        *,
        organization_id: UUID,
        chunk_id: UUID,
    ) -> tuple[UUID, UUID, str] | None:
        result = await self._session.execute(
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                DocumentChunk.content,
            ).where(
                DocumentChunk.organization_id == organization_id,
                DocumentChunk.id == chunk_id,
            )
        )
        row = result.first()
        if row is None:
            return None
        return (row[0], row[1], row[2])

    async def add_evidence_reference(
        self,
        *,
        organization_id: UUID,
        control_assessment_id: UUID,
        document_id: UUID,
        chunk_id: UUID | None,
        confidentiality_level: ConfidentialityLevel,
        snippet: str | None,
        created_by: UUID,
        touch_assessment: bool = True,
    ) -> EvidenceReferenceResponse:
        ca_res = await self._session.execute(
            select(ControlAssessment.assessment_id).where(
                ControlAssessment.organization_id == organization_id,
                ControlAssessment.id == control_assessment_id,
            )
        )
        assessment_id = ca_res.scalar_one_or_none()
        if assessment_id is not None:
            # Acquire parent assessment row lock FOR UPDATE before inserting evidence
            parent_res = await self._session.execute(
                select(ComplianceAssessment)
                .where(
                    ComplianceAssessment.organization_id == organization_id,
                    ComplianceAssessment.id == assessment_id,
                )
                .with_for_update()
            )
            parent = parent_res.scalar_one_or_none()
            if touch_assessment and parent is not None:
                if parent.status == AssessmentStatus.DRAFT:
                    parent.status = AssessmentStatus.IN_PROGRESS
                # Invalidate current computed score
                parent.overall_score = None
                parent.risk_classification = RiskClassification.NOT_SCORED
                parent.scoring_version = None

        # Check duplicate evidence attachment
        dup_stmt = select(EvidenceReference.id).where(
            EvidenceReference.organization_id == organization_id,
            EvidenceReference.control_assessment_id == control_assessment_id,
            EvidenceReference.document_id == document_id,
        )
        if chunk_id is not None:
            dup_stmt = dup_stmt.where(EvidenceReference.chunk_id == chunk_id)
        else:
            dup_stmt = dup_stmt.where(EvidenceReference.chunk_id.is_(None))

        dup_res = await self._session.execute(dup_stmt)
        if dup_res.scalar_one_or_none() is not None:
            raise ConflictError("This evidence reference has already been attached to the control.")

        ev = EvidenceReference(
            id=uuid4(),
            organization_id=organization_id,
            control_assessment_id=control_assessment_id,
            document_id=document_id,
            chunk_id=chunk_id,
            confidentiality_level=confidentiality_level,
            snippet=snippet,
            created_by=created_by,
        )
        self._session.add(ev)
        await self._session.flush()
        await self._session.refresh(ev)

        return self._to_evidence_response(ev)

    async def get_evidence_references_for_assessment(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> list[EvidenceReferenceResponse]:
        result = await self._session.execute(
            select(EvidenceReference)
            .join(
                ControlAssessment,
                EvidenceReference.control_assessment_id == ControlAssessment.id,
            )
            .where(
                EvidenceReference.organization_id == organization_id,
                ControlAssessment.organization_id == organization_id,
                ControlAssessment.assessment_id == assessment_id,
            )
            .order_by(EvidenceReference.created_at.asc())
        )
        evs = result.scalars().all()
        return [self._to_evidence_response(ev) for ev in evs]

    async def get_evidence_references_for_control(
        self,
        *,
        organization_id: UUID,
        control_assessment_id: UUID,
    ) -> list[EvidenceReferenceResponse]:
        result = await self._session.execute(
            select(EvidenceReference)
            .where(
                EvidenceReference.organization_id == organization_id,
                EvidenceReference.control_assessment_id == control_assessment_id,
            )
            .order_by(EvidenceReference.created_at.asc())
        )
        evs = result.scalars().all()
        return [self._to_evidence_response(ev) for ev in evs]

    async def save_score_snapshot_and_update_assessment(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
        scoring_version: str,
        framework_version: str,
        input_snapshot: dict[str, Any],
        raw_scores: dict[str, Any],
        overall_score: Decimal | None,
        risk_classification: RiskClassification,
        computed_by: UUID | None,
    ) -> AssessmentScoreSnapshotResponse:
        # 1. Lock the assessment row FOR UPDATE within transaction to serialize revision allocation
        stmt = (
            select(ComplianceAssessment)
            .where(
                ComplianceAssessment.organization_id == organization_id,
                ComplianceAssessment.id == assessment_id,
            )
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        assessment = result.scalar_one_or_none()
        if assessment is None:
            raise NotFoundError("The compliance assessment does not exist.")

        # 2. Concurrency-safe monotonic revision allocation
        rev_stmt = select(
            func.coalesce(func.max(AssessmentScoreSnapshot.revision_number), 0) + 1
        ).where(
            AssessmentScoreSnapshot.organization_id == organization_id,
            AssessmentScoreSnapshot.assessment_id == assessment_id,
        )
        rev_res = await self._session.execute(rev_stmt)
        next_revision = int(rev_res.scalar_one())

        # 3. Update assessment score fields
        assessment.overall_score = overall_score
        assessment.risk_classification = risk_classification
        assessment.scoring_version = scoring_version

        # 4. Insert immutable snapshot
        snapshot = AssessmentScoreSnapshot(
            id=uuid4(),
            organization_id=organization_id,
            assessment_id=assessment_id,
            revision_number=next_revision,
            scoring_version=scoring_version,
            framework_version=framework_version,
            input_snapshot=input_snapshot,
            raw_scores=raw_scores,
            overall_score=overall_score,
            risk_classification=risk_classification,
            computed_by=computed_by,
        )
        self._session.add(snapshot)
        await self._session.flush()
        await self._session.refresh(snapshot)

        return self._to_snapshot_response(snapshot)

    async def list_snapshots(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> list[AssessmentScoreSnapshotResponse]:
        result = await self._session.execute(
            select(AssessmentScoreSnapshot)
            .where(
                AssessmentScoreSnapshot.organization_id == organization_id,
                AssessmentScoreSnapshot.assessment_id == assessment_id,
            )
            .order_by(AssessmentScoreSnapshot.revision_number.asc())
        )
        snapshots = result.scalars().all()
        return [self._to_snapshot_response(s) for s in snapshots]

    async def get_latest_snapshot(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> AssessmentScoreSnapshotResponse | None:
        result = await self._session.execute(
            select(AssessmentScoreSnapshot)
            .where(
                AssessmentScoreSnapshot.organization_id == organization_id,
                AssessmentScoreSnapshot.assessment_id == assessment_id,
            )
            .order_by(AssessmentScoreSnapshot.revision_number.desc())
            .limit(1)
        )
        s = result.scalar_one_or_none()
        if s is None:
            return None
        return self._to_snapshot_response(s)

    async def create_score_override(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
        snapshot_id: UUID,
        source_revision_number: int,
        original_overall_score: Decimal | None,
        original_risk_classification: RiskClassification,
        override_overall_score: Decimal,
        override_risk_classification: RiskClassification,
        justification: str,
        overridden_by: UUID,
    ) -> ScoreOverrideResponse:
        override = ScoreOverride(
            id=uuid4(),
            organization_id=organization_id,
            assessment_id=assessment_id,
            snapshot_id=snapshot_id,
            source_revision_number=source_revision_number,
            original_overall_score=original_overall_score,
            original_risk_classification=original_risk_classification,
            override_overall_score=override_overall_score,
            override_risk_classification=override_risk_classification,
            justification=justification,
            overridden_by=overridden_by,
        )
        self._session.add(override)
        await self._session.flush()
        await self._session.refresh(override)
        return self._to_override_response(override)

    async def list_score_overrides(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> list[ScoreOverrideResponse]:
        result = await self._session.execute(
            select(ScoreOverride)
            .where(
                ScoreOverride.organization_id == organization_id,
                ScoreOverride.assessment_id == assessment_id,
            )
            .order_by(ScoreOverride.overridden_at.asc(), ScoreOverride.id.asc())
        )
        rows = result.scalars().all()
        return [self._to_override_response(r) for r in rows]

    async def get_latest_score_override(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> ScoreOverrideResponse | None:
        result = await self._session.execute(
            select(ScoreOverride)
            .where(
                ScoreOverride.organization_id == organization_id,
                ScoreOverride.assessment_id == assessment_id,
            )
            .order_by(ScoreOverride.overridden_at.desc(), ScoreOverride.id.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._to_override_response(row)

    async def finalize_assessment(
        self,
        *,
        organization_id: UUID,
        assessment_id: UUID,
    ) -> ComplianceAssessmentResponse | None:
        result = await self._session.execute(
            select(ComplianceAssessment).where(
                ComplianceAssessment.organization_id == organization_id,
                ComplianceAssessment.id == assessment_id,
            )
        )
        assessment = result.scalar_one_or_none()
        if assessment is None:
            return None
        assessment.status = AssessmentStatus.COMPLETED
        await self._session.flush()
        await self._session.refresh(assessment)
        return self._to_assessment_response(assessment)

    @staticmethod
    def _to_override_response(r: ScoreOverride) -> ScoreOverrideResponse:
        return ScoreOverrideResponse(
            id=r.id,
            organization_id=r.organization_id,
            assessment_id=r.assessment_id,
            snapshot_id=r.snapshot_id,
            source_revision_number=r.source_revision_number,
            original_overall_score=(
                Decimal(str(r.original_overall_score))
                if r.original_overall_score is not None
                else None
            ),
            original_risk_classification=r.original_risk_classification,
            override_overall_score=Decimal(str(r.override_overall_score)),
            override_risk_classification=r.override_risk_classification,
            justification=r.justification,
            overridden_by=r.overridden_by,
            overridden_at=r.overridden_at,
        )

    @staticmethod
    def _to_assessment_response(a: ComplianceAssessment) -> ComplianceAssessmentResponse:
        return ComplianceAssessmentResponse(
            id=a.id,
            organization_id=a.organization_id,
            framework_id=a.framework_id,
            title=a.title,
            status=a.status,
            overall_score=Decimal(str(a.overall_score)) if a.overall_score is not None else None,
            risk_classification=a.risk_classification,
            scoring_version=a.scoring_version,
            created_by=a.created_by,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )

    @staticmethod
    def _to_control_response(
        ca: ControlAssessment, *, evidence_count: int = 0
    ) -> ControlAssessmentResponse:
        return ControlAssessmentResponse(
            id=ca.id,
            organization_id=ca.organization_id,
            assessment_id=ca.assessment_id,
            control_id=ca.control_id,
            status=ca.status,
            effective_weight=Decimal(str(ca.effective_weight)),
            rationale=ca.rationale,
            created_at=ca.created_at,
            updated_at=ca.updated_at,
            evidence_count=evidence_count,
        )

    @staticmethod
    def _to_evidence_response(ev: EvidenceReference) -> EvidenceReferenceResponse:
        return EvidenceReferenceResponse(
            id=ev.id,
            organization_id=ev.organization_id,
            control_assessment_id=ev.control_assessment_id,
            document_id=ev.document_id,
            chunk_id=ev.chunk_id,
            confidentiality_level=ev.confidentiality_level,
            snippet=ev.snippet,
            created_by=ev.created_by,
            created_at=ev.created_at,
        )

    @staticmethod
    def _to_snapshot_response(s: AssessmentScoreSnapshot) -> AssessmentScoreSnapshotResponse:
        return AssessmentScoreSnapshotResponse(
            id=s.id,
            organization_id=s.organization_id,
            assessment_id=s.assessment_id,
            revision_number=s.revision_number,
            scoring_version=s.scoring_version,
            framework_version=s.framework_version,
            input_snapshot=dict(s.input_snapshot),
            raw_scores=dict(s.raw_scores),
            overall_score=Decimal(str(s.overall_score)) if s.overall_score is not None else None,
            risk_classification=s.risk_classification,
            computed_by=s.computed_by,
            computed_at=s.computed_at,
        )
