"""In-memory embedding provider for local development and unit tests.

Deterministic by construction: the same input string always yields the same
1024-dimensional unit vector, seeded via SHA-256 hash of the input text.
"""

from __future__ import annotations

import hashlib
import math

from app.models.ai import EmbeddingResult
from app.ports.llm_provider import EmbeddingProvider

_MOCK_EMBEDDING_MODEL = "mock:embed-v1"


def _stable_hash(payload: str) -> int:
    return int(hashlib.sha256(payload.encode()).hexdigest()[:16], 16)


def _approx_tokens(text: str) -> int:
    """~4 characters per token. Good enough for a test double; never for billing."""
    return max(1, len(text) // 4)


class MockEmbeddingProvider(EmbeddingProvider):
    """In-memory mock embedding provider producing deterministic unit vectors."""

    def __init__(self, *, dimensions: int = 1024) -> None:
        self._dimensions = dimensions
        self.embed_calls: list[list[str]] = []

    @property
    def embedding_model_id(self) -> str:
        return _MOCK_EMBEDDING_MODEL

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
