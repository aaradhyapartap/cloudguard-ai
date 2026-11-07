"""SQLAlchemy adapter for document-processing persistence."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import ProcessingChunk, ProcessingDocument
from app.models.enums import ProcessingStatus
from app.models.tenant import TenantScope
from app.ports.document_processing_repository import DocumentProcessingRepository
from app.repositories.document_chunks import DocumentChunkRepository
from app.repositories.documents import DocumentRepository
from app.repositories.tables import Document, DocumentChunk


class SQLAlchemyDocumentProcessingRepository(DocumentProcessingRepository):
    """Document-processing persistence using the tenant-scoped ORM repositories."""

    def __init__(self, *, session: AsyncSession, scope: TenantScope) -> None:
        self._session = session
        self._documents = DocumentRepository(session, scope)
        self._chunks = DocumentChunkRepository(session, scope)

    async def claim_for_processing(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
    ) -> bool:
        stmt = (
            update(Document)
            .where(
                Document.organization_id == organization_id,
                Document.id == document_id,
                Document.processing_status == ProcessingStatus.QUEUED,
            )
            .values(
                processing_status=ProcessingStatus.EXTRACTING,
                processing_error=None,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        rowcount = getattr(result, "rowcount", 0)
        return bool(rowcount == 1)

    async def get_document(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
    ) -> ProcessingDocument | None:
        document = await self._documents.get(document_id)
        if document is None or document.organization_id != organization_id:
            return None

        return ProcessingDocument(
            id=document.id,
            organization_id=document.organization_id,
            filename=document.filename,
            storage_key=document.storage_key,
            content_type=document.content_type,
            processing_status=document.processing_status,
            confidentiality_level=document.confidentiality_level,
        )

    async def set_status(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        status: ProcessingStatus,
        error: str | None,
    ) -> None:
        document = await self._documents.get(document_id)
        if document is None or document.organization_id != organization_id:
            return

        document.processing_status = status
        document.processing_error = error

    async def add_chunks(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        chunks: list[ProcessingChunk],
    ) -> None:
        entities = [
            DocumentChunk(
                id=chunk.id,
                organization_id=organization_id,
                document_id=document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                token_count=chunk.token_count,
                chunk_metadata=chunk.metadata,
            )
            for chunk in chunks
        ]
        await self._chunks.add_many(entities)
