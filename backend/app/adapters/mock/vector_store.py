"""In-memory vector store.

Exact cosine similarity over a Python list. Correct, obvious, and far too slow
for production — which is the right trade for a test double, where being able to
reason about the result matters more than throughput.

It enforces the same tenant and clearance filters as the real adapters. A test
double that skips the security filter would let a cross-tenant bug pass CI and
fail in AWS, which is the worst possible place to discover it.
"""

from __future__ import annotations

from uuid import UUID

from app.models.ai import VectorMatch, VectorRecord
from app.models.enums import ConfidentialityLevel
from app.ports.vector_store import VectorStore


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return float(dot / (left_norm * right_norm))


class InMemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._records: dict[str, VectorRecord] = {}

    async def upsert(self, records: list[VectorRecord]) -> int:
        for record in records:
            self._records[record.chunk_id] = record
        return len(records)

    async def search(
        self,
        *,
        embedding: list[float],
        organization_id: UUID,
        confidentiality_levels: tuple[ConfidentialityLevel, ...],
        top_k: int = 10,
        document_ids: list[UUID] | None = None,
    ) -> list[VectorMatch]:
        allowed = {level.value for level in confidentiality_levels}
        wanted_documents = {str(doc_id) for doc_id in document_ids} if document_ids else None

        candidates = [
            record
            for record in self._records.values()
            if record.organization_id == str(organization_id)
            and str(record.metadata.get("confidentiality_level", "internal")) in allowed
            and (wanted_documents is None or record.document_id in wanted_documents)
        ]

        scored = [
            VectorMatch(
                chunk_id=record.chunk_id,
                document_id=record.document_id,
                content=record.content,
                score=_cosine(embedding, record.embedding),
                metadata=record.metadata,
            )
            for record in candidates
        ]
        scored.sort(key=lambda match: match.score, reverse=True)
        return scored[:top_k]

    async def delete_by_document(self, *, document_id: UUID, organization_id: UUID) -> int:
        doomed = [
            chunk_id
            for chunk_id, record in self._records.items()
            if record.document_id == str(document_id)
            and record.organization_id == str(organization_id)
        ]
        for chunk_id in doomed:
            del self._records[chunk_id]
        return len(doomed)
