"""Domain models for RAG request and response."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ai import TokenUsage


class RAGRequest(BaseModel):
    """Payload for natural-language question answering over indexed documents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=2000,
            description="Natural-language question or query.",
        ),
    ]
    top_k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of nearest document chunks to retrieve for grounding (1-100).",
    )
    document_ids: list[UUID] | None = Field(
        default=None,
        max_length=50,
        description="Optional filter to scope retrieval to specific document IDs.",
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Query cannot be empty or whitespace only.")
        return trimmed

    @field_validator("document_ids")
    @classmethod
    def validate_and_deduplicate_document_ids(
        cls, value: list[UUID] | None
    ) -> list[UUID] | None:
        if value is None:
            return None
        return list(dict.fromkeys(value))


class RAGSource(BaseModel):
    """Authoritative source reference for grounded claims in the RAG response."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(description="Deterministic citation label, e.g. S1, S2.")
    chunk_id: str = Field(description="Unique identifier of the retrieved chunk.")
    document_id: str = Field(description="Unique identifier of the source document.")
    score: float = Field(description="Similarity score from vector search.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Chunk metadata (page, etc.).",
    )


class RAGResponse(BaseModel):
    """Generated answer grounded in retrieved reference chunks."""

    model_config = ConfigDict(frozen=True)

    answer: str = Field(description="Generated answer synthesized from reference sources.")
    sources: list[RAGSource] = Field(
        description="Authoritative source references retrieved for grounding.",
    )
    retrieval_count: int = Field(description="Total number of chunks retrieved.")
    model_id: str = Field(description="Model ID used for generation.")
    usage: TokenUsage | None = Field(
        default=None,
        description="Token usage for the generation request.",
    )
