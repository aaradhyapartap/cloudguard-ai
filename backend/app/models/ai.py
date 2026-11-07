"""Provider-neutral AI types.

These deliberately mention no vendor. ``GenerationRequest`` is not a Bedrock
Converse payload and not an Anthropic Messages payload — it is what *this
application* needs, and each adapter translates. That translation cost is the
price of not being locked to one provider, and it is small.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.security.model_budgets import (
    MAX_GENERATION_INPUT_BYTES,
    MAX_GENERATION_MESSAGES,
    MAX_GENERATION_OUTPUT_TOKENS,
    MAX_STOP_SEQUENCE_CHARACTERS,
    MAX_STOP_SEQUENCES,
    require_generation_envelope,
)


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_GENERATION_INPUT_BYTES)


class GenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: list[Message] = Field(min_length=1, max_length=MAX_GENERATION_MESSAGES)
    system_prompt: str | None = Field(default=None, max_length=MAX_GENERATION_INPUT_BYTES)
    max_tokens: int = Field(default=2048, ge=1, le=MAX_GENERATION_OUTPUT_TOKENS)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    # A JSON schema the response must satisfy. Schema-constrained output makes
    # parsing deterministic instead of regex archaeology.
    response_schema: dict[str, Any] | None = None
    stop_sequences: list[str] = Field(default_factory=list, max_length=MAX_STOP_SEQUENCES)

    @model_validator(mode="after")
    def _bounded_envelope(self) -> GenerationRequest:
        if any(len(stop) > MAX_STOP_SEQUENCE_CHARACTERS for stop in self.stop_sequences):
            raise ValueError("A stop sequence exceeds the resource budget.")
        text_characters = sum(len(message.content) for message in self.messages)
        text_characters += len(self.system_prompt or "") + sum(map(len, self.stop_sequences))
        if text_characters > MAX_GENERATION_INPUT_BYTES:
            raise ValueError("The model text exceeds the resource budget.")
        require_generation_envelope(
            self.model_dump(mode="json"), max_bytes=MAX_GENERATION_INPUT_BYTES,
        )
        return self


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    @property
    def is_complete(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None


class GenerationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    model_id: str
    usage: TokenUsage | None = None
    stop_reason: str
    latency_ms: int
    # Provider-neutral safety metadata. Any intervention fails closed before a
    # response can be consumed by an application service.
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
