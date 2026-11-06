"""Application service for secure semantic document chunk retrieval."""

from __future__ import annotations

import time

from app.core.errors import UpstreamError, ValidationError
from app.core.logging import get_logger
from app.models.ai import VectorMatch
from app.models.principal import Principal
from app.models.retrieval import RetrievalRequest, RetrievalResponse
from app.models.telemetry import CostEstimate
from app.ports.llm_provider import EmbeddingProvider
from app.ports.telemetry import TelemetryEmitter
from app.ports.vector_store import VectorStore, validate_top_k
from app.security.projections import safe_source_metadata
from app.security.sensitive_data import minimize_model_text
from app.telemetry.isolation import safe_telemetry_call

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
        telemetry: TelemetryEmitter | None = None,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._telemetry = telemetry

    async def search(
        self,
        *,
        principal: Principal,
        request: RetrievalRequest,
    ) -> RetrievalResponse:
        """Search nearest document chunks for the authenticated caller."""
        start_time = time.perf_counter()
        query = request.query.strip()
        if not query:
            raise ValidationError("Query cannot be empty or whitespace only.")

        try:
            validate_top_k(request.top_k)
        except ValueError as exc:
            raise ValidationError("The requested result count is invalid.") from exc

        # 1. Generate query embedding
        try:
            embedding_result = await self._embedding_provider.embed(
                [minimize_model_text(query, surface="RETRIEVAL_QUERY")]
            )
        except UpstreamError:
            if self._telemetry is not None:
                safe_telemetry_call(
                    self._telemetry.record_counter,
                    "RetrievalErrors",
                    1.0,
                    dimensions={"Operation": "retrieval_search", "Status": "error"},
                )
            raise
        except Exception as exc:
            if self._telemetry is not None:
                safe_telemetry_call(
                    self._telemetry.record_counter,
                    "RetrievalErrors",
                    1.0,
                    dimensions={"Operation": "retrieval_search", "Status": "error"},
                )
            logger.error(
                "retrieval_query_embedding_failed",
                organization_id=str(principal.organization_id),
                error_type=type(exc).__name__,
            )
            raise UpstreamError("The query could not be embedded.") from exc

        # Record query embedding token usage if available
        embedding_cost: CostEstimate | None = None
        if self._telemetry is not None and getattr(embedding_result, "input_tokens", None):
            raw_cost = safe_telemetry_call(
                self._telemetry.record_token_usage,
                model_id=embedding_result.model_id,
                input_tokens=embedding_result.input_tokens,
                output_tokens=0,
                operation="retrieval_search",
            )
            if isinstance(raw_cost, CostEstimate):
                embedding_cost = raw_cost

        if len(embedding_result.vectors) != 1:
            if self._telemetry is not None:
                safe_telemetry_call(
                    self._telemetry.record_counter,
                    "RetrievalErrors",
                    1.0,
                    dimensions={"Operation": "retrieval_search", "Status": "error"},
                )
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
            if self._telemetry is not None:
                safe_telemetry_call(
                    self._telemetry.record_counter,
                    "RetrievalErrors",
                    1.0,
                    dimensions={"Operation": "retrieval_search", "Status": "error"},
                )
            raise
        except Exception as exc:
            if self._telemetry is not None:
                safe_telemetry_call(
                    self._telemetry.record_counter,
                    "RetrievalErrors",
                    1.0,
                    dimensions={"Operation": "retrieval_search", "Status": "error"},
                )
            logger.error(
                "retrieval_vector_search_failed",
                organization_id=str(principal.organization_id),
                error_type=type(exc).__name__,
            )
            raise UpstreamError("The vector search could not be completed.") from exc

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        if self._telemetry is not None:
            safe_telemetry_call(
                self._telemetry.record_latency,
                "retrieval_search",
                duration_ms,
                dimensions={"Operation": "retrieval_search", "Status": "success"},
            )
            safe_telemetry_call(
                self._telemetry.record_counter,
                "RetrievalMatchesCount",
                float(len(matches)),
                dimensions={"Operation": "retrieval_search"},
            )

        logger.info(
            "retrieval_search_completed",
            organization_id=str(principal.organization_id),
            user_id=str(principal.user_id),
            top_k=request.top_k,
            result_count=len(matches),
        )

        return RetrievalResponse(
            matches=[
                match.model_copy(update={"metadata": safe_source_metadata(match.metadata)})
                for match in matches
            ],
            total=len(matches),
            cost=embedding_cost,
        )
