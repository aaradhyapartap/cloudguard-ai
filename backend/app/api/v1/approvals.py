"""Human Approval API for Phase 7.3."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.adapters.local.approval_repository import (
    SQLAlchemyApprovalRepository,
)
from app.api.deps import PrincipalDep, SessionDep, requires
from app.models.approval import (
    ApprovalDecisionRequest,
    DecidedApproval,
    PendingApproval,
)
from app.security.authz import Permission
from app.services.approval import ApprovalService

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _service(session: SessionDep) -> ApprovalService:
    return ApprovalService(
        repository=SQLAlchemyApprovalRepository(session)
    )


@router.get(
    "",
    response_model=list[PendingApproval],
    status_code=status.HTTP_200_OK,
    dependencies=[requires(Permission.APPROVAL_READ)],
    summary="List pending approvals",
)
async def list_pending_approvals(
    session: SessionDep,
    principal: PrincipalDep,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[PendingApproval]:
    """List the caller tenant's pending Manager approval queue."""
    service = _service(session)
    return await service.list_pending(
        principal=principal,
        limit=limit,
    )


@router.get(
    "/{approval_id}",
    response_model=PendingApproval | DecidedApproval,
    status_code=status.HTTP_200_OK,
    dependencies=[requires(Permission.APPROVAL_READ)],
    summary="Get approval detail",
)
async def get_approval(
    approval_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> PendingApproval | DecidedApproval:
    """Fetch a tenant-scoped approval without exposing its task token."""
    service = _service(session)
    return await service.get_approval(
        principal=principal,
        approval_id=approval_id,
    )


@router.post(
    "/{approval_id}/decision",
    response_model=DecidedApproval,
    status_code=status.HTTP_200_OK,
    dependencies=[requires(Permission.APPROVAL_DECIDE)],
    summary="Record Manager approval decision",
)
async def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    session: SessionDep,
    principal: PrincipalDep,
) -> DecidedApproval:
    """Record the first authorized Manager decision.

    Workflow callback/resume is intentionally deferred to Phase 7.4.
    """
    service = _service(session)
    return await service.decide(
        principal=principal,
        approval_id=approval_id,
        request=payload,
    )
