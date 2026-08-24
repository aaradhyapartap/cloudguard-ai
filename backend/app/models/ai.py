"""Provider-neutral AI types.

These deliberately mention no vendor. ``GenerationRequest`` is not a Bedrock
Converse payload and not an Anthropic Messages payload — it is what *this
application* needs, and each adapter translates. That translation cost is the
price of not being locked to one provider, and it is small.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str


class GenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: list[Message]
    system_prompt: str | None = None
    max_tokens: int = 2048
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    # A JSON schema the response must satisfy. Schema-constrained output makes
    # parsing deterministic instead of regex archaeology.
    response_schema: dict[str, Any] | None = None
    stop_sequences: list[str] = Field(default_factory=list)


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class GenerationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    model_id: str
    usage: TokenUsage
    stop_reason: str
    latency_ms: int
    # Populated by the Bedrock adapter in Phase 8 when a guardrail intervenes.
    guardrail_action: str | None = None
    generated_at: datetime


class EmbeddingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    vectors: list[list[float]]
    model_id: str
    dimensions: int
    input_tokens: int


class VectorRecord(BaseModel):
    """A chunk on its way into the vector store."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    organization_id: str
    embedding: list[float]
    content: str
    # Filterable at query time. organization_id and confidentiality_level are
    # always applied server-side, never taken from the caller's request body.
    metadata: dict[str, Any] = Field(default_factory=dict)


class VectorMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class DomainEvent(BaseModel):
    """Anything published to the EventBridge bus."""

    model_config = ConfigDict(frozen=True)

    event_type: str
    organization_id: str
    payload: dict[str, Any]
    occurred_at: datetime
    correlation_id: str | None = None
