"""Application service for secure semantic document chunk retrieval."""

from __future__ import annotations

from app.core.errors import UpstreamError, ValidationError
from app.core.logging import get_logger
from app.models.ai import VectorMatch
from app.models.principal import Principal
from app.models.retrieval import RetrievalRequest, RetrievalResponse
from app.ports.llm_provider import EmbeddingProvider
from app.ports.vector_store import VectorStore, validate_top_k

logger = get_logger(__name__)


class RetrievalService:
    """Coordinates embedding generation and vector search for an authenticated principal.

    Security invariants:
    - ``organization_id`` is strictly derived from the authenticated ``Principal``.
    - ``confidentiality_levels`` are strictly computed from the ``Principal``'s
      role clearance ceiling, never taken from user-supplied request headers/filters.
    - Raw vector embeddings and document query texts are never logged.
    """

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store

    async def search(
        self,
        *,
        principal: Principal,
        request: RetrievalRequest,
    ) -> RetrievalResponse:
        """Search nearest document chunks for the authenticated caller."""
        query = request.query.strip()
        if not query:
            raise ValidationError("Query cannot be empty or whitespace only.")

        try:
            validate_top_k(request.top_k)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        # 1. Generate query embedding
        try:
            embedding_result = await self._embedding_provider.embed([query])
        except UpstreamError:
            raise
        except Exception as exc:
            logger.error(
                "retrieval_query_embedding_failed",
                organization_id=str(principal.organization_id),
                error_type=type(exc).__name__,
            )
            raise UpstreamError("The query could not be embedded.") from exc

        if len(embedding_result.vectors) != 1:
            logger.error(
                "retrieval_embedding_count_mismatch",
                organization_id=str(principal.organization_id),
                expected=1,
                actual=len(embedding_result.vectors),
            )
            raise UpstreamError("The query could not be embedded.")

        query_vector = embedding_result.vectors[0]

        # 2. Query VectorStore using principal's organization and clearance
        try:
            matches: list[VectorMatch] = await self._vector_store.search(
                embedding=query_vector,
                organization_id=principal.organization_id,
                confidentiality_levels=principal.visible_confidentiality_levels,
                top_k=request.top_k,
                document_ids=request.document_ids,
            )
        except UpstreamError:
            raise
        except Exception as exc:
            logger.error(
                "retrieval_vector_search_failed",
                organization_id=str(principal.organization_id),
                error_type=type(exc).__name__,
            )
            raise UpstreamError("The vector search could not be completed.") from exc

        logger.info(
            "retrieval_search_completed",
            organization_id=str(principal.organization_id),
            user_id=str(principal.user_id),
            top_k=request.top_k,
            result_count=len(matches),
        )

        return RetrievalResponse(
            matches=matches,
            total=len(matches),
        )
