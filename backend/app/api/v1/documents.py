"""Document ingestion endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import ContainerDep, PrincipalDep, SessionDep, requires
from app.models.common import Page
from app.models.documents import (
    DocumentCreateRequest,
    DocumentResponse,
    DocumentUploadResponse,
)
from app.security.authz import Permission
from app.services.documents import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


def _service(
    *,
    session: SessionDep,
    principal: PrincipalDep,
    container: ContainerDep,
) -> DocumentService:
    return DocumentService(
        session=session,
        principal=principal,
        document_store=container.documents,
        event_publisher=container.events,
    )


@router.post(
    "",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a document upload",
    dependencies=[requires(Permission.DOCUMENT_UPLOAD)],
)
async def register_document(
    payload: DocumentCreateRequest,
    session: SessionDep,
    principal: PrincipalDep,
    container: ContainerDep,
) -> DocumentUploadResponse:
    service = _service(
        session=session,
        principal=principal,
        container=container,
    )
    return await service.register_upload(payload)


@router.get(
    "",
    response_model=Page[DocumentResponse],
    summary="List documents",
    dependencies=[requires(Permission.DOCUMENT_READ)],
)
async def list_documents(
    session: SessionDep,
    principal: PrincipalDep,
    container: ContainerDep,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Page[DocumentResponse]:
    service = _service(
        session=session,
        principal=principal,
        container=container,
    )

    items = await service.list(limit=limit, offset=offset)
    total = await service.count()

    return Page(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get a document",
    dependencies=[requires(Permission.DOCUMENT_READ)],
)
async def get_document(
    document_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
    container: ContainerDep,
) -> DocumentResponse:
    service = _service(
        session=session,
        principal=principal,
        container=container,
    )
    return await service.get(document_id)
