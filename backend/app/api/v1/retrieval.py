"""Retrieval endpoints for semantic vector search over document chunks."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import ContainerDep, PrincipalDep, requires
from app.models.retrieval import RetrievalRequest, RetrievalResponse
from app.security.authz import Permission
from app.services.retrieval import RetrievalService

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


def _service(container: ContainerDep) -> RetrievalService:
    return RetrievalService(
        embedding_provider=container.embeddings,
        vector_store=container.vectors,
    )


@router.post(
    "/search",
    response_model=RetrievalResponse,
    status_code=status.HTTP_200_OK,
    summary="Semantic vector search across indexed document chunks",
    dependencies=[requires(Permission.DOCUMENT_READ)],
)
async def search_chunks(
    payload: RetrievalRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> RetrievalResponse:
    """Search nearest document chunks within the authenticated caller's tenant and clearance."""
    service = _service(container)
    return await service.search(principal=principal, request=payload)
