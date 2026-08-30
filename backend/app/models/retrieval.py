"""Domain models for semantic retrieval requests and responses."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ai import VectorMatch


class RetrievalRequest(BaseModel):
    """Payload for natural-language retrieval over indexed document chunks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=2000,
            description="Natural-language search query.",
        ),
    ]
    top_k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of nearest chunks to return (1-100).",
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
        # Deduplicate preserving order
        return list(dict.fromkeys(value))


class RetrievalResponse(BaseModel):
    """Response containing nearest matching document chunks without exposing raw vectors."""

    model_config = ConfigDict(frozen=True)

    matches: list[VectorMatch]
    total: int
