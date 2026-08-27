"""Tenant-scoped repository for extracted document chunks."""

from __future__ import annotations

from uuid import UUID

from app.repositories.base import TenantRepository
from app.repositories.tables import DocumentChunk


class DocumentChunkRepository(TenantRepository[DocumentChunk]):
    model = DocumentChunk

    async def list_for_document(self, document_id: UUID) -> list[DocumentChunk]:
        result = await self._session.execute(
            self._scoped()
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return list(result.scalars().all())
