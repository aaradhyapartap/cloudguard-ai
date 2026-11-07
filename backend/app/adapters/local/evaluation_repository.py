"""SQLAlchemy and in-memory persistence adapters for tenant-scoped evaluation runs."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evaluation import (
    CategoryEvaluationSummary,
    EvaluationCaseResult,
    EvaluationExecutionMode,
    EvaluationMetricsSummary,
    EvaluationRunCreate,
    EvaluationRunRead,
    EvaluationRunSummaryRead,
)
from app.ports.evaluation_repository import EvaluationRepository
from app.repositories.tables import EvaluationRun


class SQLAlchemyEvaluationRepository(EvaluationRepository):
    """PostgreSQL-backed repository for tenant-scoped evaluation runs.

    Expects an ``AsyncSession`` already opened within ``tenant_session`` so both
    explicit ``organization_id`` predicates and PostgreSQL forced RLS apply.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, payload: EvaluationRunCreate) -> EvaluationRunRead:
        row = EvaluationRun(
            organization_id=payload.organization_id,
            triggered_by=payload.triggered_by,
            dataset_version=payload.dataset_version,
            execution_mode=payload.execution_mode.value,
            embedding_model_id=payload.embedding_model_id,
            generation_model_id=payload.generation_model_id,
            reviewer_model_id=payload.reviewer_model_id,
            judge_model_id=payload.judge_model_id,
            security_gate_passed=payload.security_gate_passed,
            quality_gate_advisory_passed=payload.quality_gate_advisory_passed,
            metrics_summary=payload.metrics_summary.model_dump(mode="json"),
            category_breakdown=[
                item.model_dump(mode="json") for item in payload.category_breakdown
            ],
            case_results=[
                item.model_dump(mode="json") for item in payload.case_results
            ],
            provenance_notes=list(payload.provenance_notes),
            started_at=payload.started_at,
            completed_at=payload.completed_at,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return self._to_read(row)

    async def get_run(
        self,
        *,
        organization_id: UUID,
        run_id: UUID,
        triggered_by_filter: UUID | None = None,
    ) -> EvaluationRunRead | None:
        stmt = select(EvaluationRun).where(
            EvaluationRun.organization_id == organization_id,
            EvaluationRun.id == run_id,
        )
        if triggered_by_filter is not None:
            stmt = stmt.where(EvaluationRun.triggered_by == triggered_by_filter)

        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._to_read(row)

    async def list_runs(
        self,
        *,
        organization_id: UUID,
        triggered_by_filter: UUID | None = None,
        limit: int = 25,
    ) -> list[EvaluationRunSummaryRead]:
        if limit < 1 or limit > 100:
            raise ValueError("Evaluation run list limit must be between 1 and 100.")

        stmt = select(EvaluationRun).where(
            EvaluationRun.organization_id == organization_id
        )
        if triggered_by_filter is not None:
            stmt = stmt.where(EvaluationRun.triggered_by == triggered_by_filter)

        stmt = stmt.order_by(
            EvaluationRun.created_at.desc(),
            EvaluationRun.id.desc(),
        ).limit(limit)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_summary(row) for row in rows]

    @staticmethod
    def _to_summary(row: EvaluationRun) -> EvaluationRunSummaryRead:
        return EvaluationRunSummaryRead(
            id=row.id,
            organization_id=row.organization_id,
            triggered_by=row.triggered_by,
            dataset_version=row.dataset_version,
            execution_mode=EvaluationExecutionMode(row.execution_mode),
            embedding_model_id=row.embedding_model_id,
            generation_model_id=row.generation_model_id,
            reviewer_model_id=row.reviewer_model_id,
            judge_model_id=row.judge_model_id,
            security_gate_passed=row.security_gate_passed,
            quality_gate_advisory_passed=row.quality_gate_advisory_passed,
            metrics_summary=EvaluationMetricsSummary.model_validate(
                row.metrics_summary
            ),
            category_breakdown=tuple(
                CategoryEvaluationSummary.model_validate(item)
                for item in row.category_breakdown
            ),
            provenance_notes=tuple(str(note) for note in row.provenance_notes),
            started_at=row.started_at,
            completed_at=row.completed_at,
            created_at=row.created_at,
        )

    @classmethod
    def _to_read(cls, row: EvaluationRun) -> EvaluationRunRead:
        summary = cls._to_summary(row)
        return EvaluationRunRead(
            **summary.model_dump(),
            case_results=tuple(
                EvaluationCaseResult.model_validate(item) for item in row.case_results
            ),
        )


class InMemoryEvaluationRepository(EvaluationRepository):
    """In-memory tenant-scoped evaluation repository for isolated unit tests."""

    def __init__(self) -> None:
        self._runs: dict[tuple[UUID, UUID], EvaluationRunRead] = {}

    async def create_run(self, payload: EvaluationRunCreate) -> EvaluationRunRead:
        run_id = uuid4()
        now = datetime.now(UTC)
        record = EvaluationRunRead(
            id=run_id,
            organization_id=payload.organization_id,
            triggered_by=payload.triggered_by,
            dataset_version=payload.dataset_version,
            execution_mode=payload.execution_mode,
            embedding_model_id=payload.embedding_model_id,
            generation_model_id=payload.generation_model_id,
            reviewer_model_id=payload.reviewer_model_id,
            judge_model_id=payload.judge_model_id,
            security_gate_passed=payload.security_gate_passed,
            quality_gate_advisory_passed=payload.quality_gate_advisory_passed,
            metrics_summary=payload.metrics_summary,
            category_breakdown=payload.category_breakdown,
            case_results=payload.case_results,
            provenance_notes=payload.provenance_notes,
            started_at=payload.started_at,
            completed_at=payload.completed_at,
            created_at=now,
        )
        self._runs[(payload.organization_id, run_id)] = record
        return record

    async def get_run(
        self,
        *,
        organization_id: UUID,
        run_id: UUID,
        triggered_by_filter: UUID | None = None,
    ) -> EvaluationRunRead | None:
        record = self._runs.get((organization_id, run_id))
        if record is None:
            return None
        if triggered_by_filter is not None and record.triggered_by != triggered_by_filter:
            return None
        return record

    async def list_runs(
        self,
        *,
        organization_id: UUID,
        triggered_by_filter: UUID | None = None,
        limit: int = 25,
    ) -> list[EvaluationRunSummaryRead]:
        if limit < 1 or limit > 100:
            raise ValueError("Evaluation run list limit must be between 1 and 100.")
        matching = [
            record
            for (org_id, _), record in self._runs.items()
            if org_id == organization_id
            and (
                triggered_by_filter is None
                or record.triggered_by == triggered_by_filter
            )
        ]
        matching.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return [
            EvaluationRunSummaryRead.model_validate(
                record.model_dump(exclude={"case_results"})
            )
            for record in matching[:limit]
        ]

