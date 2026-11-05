"""AI Evaluation & Analytics API routes for Phase 9."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.adapters.local.evaluation_repository import SQLAlchemyEvaluationRepository
from app.api.deps import PrincipalDep, SessionDep, requires, requires_any
from app.models.evaluation import (
    EvaluationDatasetMetadataResponse,
    EvaluationRunListResponse,
    EvaluationRunRead,
    TriggerEvaluationRequest,
)
from app.security.authz import Permission
from app.services.evaluation import EvaluationService

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


def _service(session: SessionDep) -> EvaluationService:
    return EvaluationService(repository=SQLAlchemyEvaluationRepository(session))


@router.get(
    "/dataset",
    response_model=EvaluationDatasetMetadataResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[
        requires_any(Permission.ANALYTICS_READ_OWN, Permission.ANALYTICS_READ_TEAM)
    ],
    summary="Get golden dataset and metric metadata",
)
async def get_evaluation_dataset_metadata(
    session: SessionDep,
    principal: PrincipalDep,
) -> EvaluationDatasetMetadataResponse:
    """Return versioned golden dataset counts, metric formulas, and provenance limits."""
    service = _service(session)
    return service.get_dataset_metadata(principal=principal)


@router.get(
    "/runs",
    response_model=EvaluationRunListResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[
        requires_any(Permission.ANALYTICS_READ_OWN, Permission.ANALYTICS_READ_TEAM)
    ],
    summary="List tenant evaluation runs",
)
async def list_evaluation_runs(
    session: SessionDep,
    principal: PrincipalDep,
    limit: int = Query(default=25, ge=1, le=100),
) -> EvaluationRunListResponse:
    """List evaluation runs (`team` scope for Manager/Admin, `own` for Analyst)."""
    service = _service(session)
    return await service.list_runs(principal=principal, limit=limit)


@router.get(
    "/runs/{run_id}",
    response_model=EvaluationRunRead,
    status_code=status.HTTP_200_OK,
    dependencies=[
        requires_any(Permission.ANALYTICS_READ_OWN, Permission.ANALYTICS_READ_TEAM)
    ],
    summary="Get evaluation run detail",
)
async def get_evaluation_run(
    run_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> EvaluationRunRead:
    """Fetch a single tenant-scoped evaluation run including case-level outcomes."""
    service = _service(session)
    return await service.get_run(principal=principal, run_id=run_id)


@router.post(
    "/runs",
    response_model=EvaluationRunRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        requires_any(Permission.ANALYTICS_READ_OWN, Permission.ANALYTICS_READ_TEAM),
        requires(Permission.DOCUMENT_READ),
        requires(Permission.AI_QUERY),
    ],
    summary="Trigger an AI & security evaluation run",
)
async def trigger_evaluation_run(
    payload: TriggerEvaluationRequest,
    session: SessionDep,
    principal: PrincipalDep,
) -> EvaluationRunRead:
    """Execute the reproducible evaluation suite and store the tenant-scoped run."""
    service = _service(session)
    return await service.trigger_run(principal=principal, request=payload)

