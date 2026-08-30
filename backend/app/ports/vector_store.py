"""Port: vector storage and similarity search.

Two real adapters are planned (ADR-0002): ``pgvector`` for the MVP and
``s3_vectors`` for the Phase 11 benchmark. Two working adapters prove the
abstraction; one adapter plus an interface proves nothing.

Security invariant, enforced by the signature itself: ``search`` takes
``organization_id`` and ``confidentiality_levels`` as **required, separate**
arguments rather than as entries in a free-form filter dict. A caller cannot
forget them, and no adapter can be written that ignores them without the
omission being obvious in review.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from uuid import UUID

from app.models.ai import VectorMatch, VectorRecord
from app.models.enums import ConfidentialityLevel

EXPECTED_EMBEDDING_DIMENSIONS = 1024
MIN_TOP_K = 1
MAX_TOP_K = 100


def validate_embedding(embedding: list[float], *, label: str = "embedding") -> None:
    """Validate vector dimensionality and finite numeric values without leaking vectors."""
    if len(embedding) != EXPECTED_EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Invalid {label} dimensions: "
            f"expected {EXPECTED_EMBEDDING_DIMENSIONS}, got {len(embedding)}"
        )
    for idx, val in enumerate(embedding):
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise ValueError(
                f"Invalid {label} value at index {idx}: must be a finite numeric value."
            )


def validate_top_k(top_k: int) -> None:
    """Ensure top_k is bounded to a safe positive range."""
    if (
        isinstance(top_k, bool)
        or not isinstance(top_k, int)
        or top_k < MIN_TOP_K
        or top_k > MAX_TOP_K
    ):
        raise ValueError(
            f"top_k must be an integer between {MIN_TOP_K} and {MAX_TOP_K}, got {top_k}"
        )


class VectorStore(ABC):
    @abstractmethod
    async def upsert(self, records: list[VectorRecord]) -> int:
        """Insert or replace records. Returns the number written."""

    @abstractmethod
    async def search(
        self,
        *,
        embedding: list[float],
        organization_id: UUID,
        confidentiality_levels: tuple[ConfidentialityLevel, ...],
        top_k: int = 10,
        document_ids: list[UUID] | None = None,
    ) -> list[VectorMatch]:
        """Nearest neighbours within the caller's tenant and clearance.

        The tenant and clearance arguments are mandatory. This is a deliberate
        API design choice: making a security control an optional keyword is how
        it eventually gets omitted.
        """

    @abstractmethod
    async def delete_by_document(self, *, document_id: UUID, organization_id: UUID) -> int:
        """Remove every vector for a document. Returns the number of vectors cleared."""
