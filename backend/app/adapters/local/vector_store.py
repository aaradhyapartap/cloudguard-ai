"""Local SQLAlchemy vector store implementation using pgvector.

Provides tenant-scoped vector persistence and cosine similarity search for local
development and integration testing against PostgreSQL with pgvector.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select, update

from app.models.ai import VectorMatch, VectorRecord
from app.models.enums import ConfidentialityLevel
from app.ports.vector_store import (
    VectorStore,
    validate_embedding,
    validate_top_k,
)
from app.repositories.database import tenant_session
from app.repositories.tables import Document, DocumentChunk


class SQLAlchemyVectorStore(VectorStore):
    """SQLAlchemy vector store with PostgreSQL pgvector support."""

    async def upsert(self, records: list[VectorRecord]) -> int:
        """Insert or replace vector embeddings on existing document chunks.

        Enforces tenant isolation by running within tenant-scoped sessions.
        Requires organization_id, document_id, and chunk_id matching for row updates.
        Validates embedding dimensions/values and returns the number of chunks updated.
        """
        if not records:
            return 0

        # Validate dimensions and values before beginning
        for record in records:
            validate_embedding(
                record.embedding,
                label=f"chunk {record.chunk_id} embedding",
            )

        # Group records by organization_id to batch inside tenant transactions
        grouped: dict[str, list[VectorRecord]] = defaultdict(list)
        for record in records:
            grouped[record.organization_id].append(record)

        total_updated = 0
        for org_id_str, org_records in grouped.items():
            org_id = UUID(org_id_str)
            async with tenant_session(org_id) as session:
                for record in org_records:
                    stmt = (
                        update(DocumentChunk)
                        .where(
                            DocumentChunk.organization_id == org_id,
                            DocumentChunk.document_id == UUID(record.document_id),
                            DocumentChunk.id == UUID(record.chunk_id),
                        )
                        .values(embedding=record.embedding)
                    )
                    res = await session.execute(stmt)
                    total_updated += getattr(res, "rowcount", 0)

        return total_updated

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

        Enforces dual-layer security:
        1. Explicit WHERE organization_id = :org_id and document clearance checks.
        2. Transaction-level PostgreSQL RLS via tenant_session().
        """
        validate_embedding(embedding, label="query embedding")
        validate_top_k(top_k)

        if not confidentiality_levels:
            return []

        async with tenant_session(organization_id) as session:
            distance_expr = DocumentChunk.embedding.cosine_distance(embedding)
            score_expr = (1.0 - distance_expr).label("score")

            stmt = (
                select(
                    DocumentChunk.id,
                    DocumentChunk.document_id,
                    DocumentChunk.content,
                    DocumentChunk.chunk_metadata,
                    score_expr,
                )
                .join(
                    Document,
                    (Document.id == DocumentChunk.document_id)
                    & (Document.organization_id == DocumentChunk.organization_id),
                )
                .where(
                    DocumentChunk.organization_id == organization_id,
                    DocumentChunk.embedding.is_not(None),
                    Document.confidentiality_level.in_(confidentiality_levels),
                )
            )

            if document_ids:
                stmt = stmt.where(DocumentChunk.document_id.in_(document_ids))

            stmt = stmt.order_by(distance_expr.asc()).limit(top_k)

            result = await session.execute(stmt)
            rows = result.all()

            return [
                VectorMatch(
                    chunk_id=str(row[0]),
                    document_id=str(row[1]),
                    content=str(row[2]),
                    metadata=dict(row[3]) if row[3] else {},
                    score=float(row[4]),
                )
                for row in rows
            ]

    async def delete_by_document(self, *, document_id: UUID, organization_id: UUID) -> int:
        """Remove every vector for a document within the caller's tenant without deleting chunks."""
        async with tenant_session(organization_id) as session:
            stmt = (
                update(DocumentChunk)
                .where(
                    DocumentChunk.organization_id == organization_id,
                    DocumentChunk.document_id == document_id,
                    DocumentChunk.embedding.is_not(None),
                )
                .values(embedding=None)
            )
            res = await session.execute(stmt)
            return int(getattr(res, "rowcount", 0))
