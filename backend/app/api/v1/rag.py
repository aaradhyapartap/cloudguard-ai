"""API routes for retrieval-augmented generation question answering."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import ContainerDep, PrincipalDep, requires
from app.models.rag import RAGRequest, RAGResponse
from app.security.authz import Permission
from app.services.rag import RAGService
from app.services.retrieval import RetrievalService

router = APIRouter(prefix="/rag", tags=["rag"])


def _service(container: ContainerDep) -> RAGService:
    retrieval_service = RetrievalService(
        embedding_provider=container.embeddings,
        vector_store=container.vectors,
    )
    return RAGService(
        retrieval_service=retrieval_service,
        llm_provider=container.llm,
    )


@router.post(
    "/query",
    response_model=RAGResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate a grounded answer for a natural-language query",
    dependencies=[requires(Permission.DOCUMENT_READ)],
)
async def query_rag(
    payload: RAGRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> RAGResponse:
    """Retrieve tenant document chunks within caller clearance and generate a cited answer."""
    service = _service(container)
    return await service.generate_answer(principal=principal, request=payload)
