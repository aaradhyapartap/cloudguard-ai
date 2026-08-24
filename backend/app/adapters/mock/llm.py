"""In-memory LLM provider for local development and unit tests.

Deterministic by construction: the same request always yields the same
response, keyed on a hash of the request. That determinism is the entire point —
a test that sometimes passes is worse than no test.

This is not the ``recorded`` provider. That one (Phase 4) replays real Bedrock
responses from cassettes. This one fabricates plausible shapes so the pipeline
can be exercised end to end with no network at all.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime

from app.models.ai import (
    EmbeddingResult,
    GenerationRequest,
    GenerationResponse,
    TokenUsage,
)
from app.ports.llm_provider import LLMProvider

_MOCK_CHAT_MODEL = "mock:chat-v1"
_MOCK_EMBEDDING_MODEL = "mock:embed-v1"


def _stable_hash(payload: str) -> int:
    return int(hashlib.sha256(payload.encode()).hexdigest()[:16], 16)


def _approx_tokens(text: str) -> int:
    """~4 characters per token. Good enough for a test double; never for billing."""
    return max(1, len(text) // 4)


class MockLLMProvider(LLMProvider):
    def __init__(self, *, dimensions: int = 1024) -> None:
        self._dimensions = dimensions
        self.generate_calls: list[GenerationRequest] = []
        self.embed_calls: list[list[str]] = []

    @property
    def chat_model_id(self) -> str:
        return _MOCK_CHAT_MODEL

    @property
    def embedding_model_id(self) -> str:
        return _MOCK_EMBEDDING_MODEL

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.generate_calls.append(request)
        prompt = "\n".join(message.content for message in request.messages)

        if request.response_schema is not None:
            content = json.dumps(_schema_stub(request.response_schema), sort_keys=True)
        else:
            content = (
                "[mock] No model was called. Set LLM_PROVIDER=bedrock for real "
                f"inference. Prompt digest: {_stable_hash(prompt) % 10**8:08d}"
            )

        return GenerationResponse(
            content=content,
            model_id=_MOCK_CHAT_MODEL,
            usage=TokenUsage(
                input_tokens=_approx_tokens(prompt),
                output_tokens=_approx_tokens(content),
            ),
            stop_reason="end_turn",
            latency_ms=1,
            generated_at=datetime.now(UTC),
        )

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        self.embed_calls.append(texts)
        return EmbeddingResult(
            vectors=[self._deterministic_vector(text) for text in texts],
            model_id=_MOCK_EMBEDDING_MODEL,
            dimensions=self._dimensions,
            input_tokens=sum(_approx_tokens(text) for text in texts),
        )

    def _deterministic_vector(self, text: str) -> list[float]:
        """Unit-norm pseudo-embedding.

        Similar strings produce similar vectors because the seed is derived from
        the text, which makes retrieval tests meaningful rather than random.
        """
        seed = _stable_hash(text)
        raw = [
            math.sin(seed * (index + 1) * 0.000_001) for index in range(self._dimensions)
        ]
        norm = math.sqrt(sum(value * value for value in raw)) or 1.0
        return [value / norm for value in raw]


def _schema_stub(schema: dict[str, object]) -> dict[str, object]:
    """Build a minimal object satisfying a JSON schema's top-level properties."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    stub: dict[str, object] = {}
    for name, definition in properties.items():
        kind = definition.get("type") if isinstance(definition, dict) else "string"
        stub[name] = {
            "string": "",
            "integer": 0,
            "number": 0.0,
            "boolean": False,
            "array": [],
            "object": {},
        }.get(str(kind), None)
    return stub
