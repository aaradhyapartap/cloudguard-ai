"""Internal document extraction and chunking service."""

from __future__ import annotations

from builtins import list as list_type
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.ai import DomainEvent
from app.models.enums import ProcessingStatus
from app.models.tenant import TenantScope
from app.ports.document_store import DocumentStore
from app.ports.event_publisher import EventPublisher
from app.repositories.document_chunks import DocumentChunkRepository
from app.repositories.documents import DocumentRepository
from app.repositories.tables import Document, DocumentChunk

TEXT_CHUNK_SIZE = 1000


class DocumentProcessingService:
    """Processes uploaded documents inside an explicit tenant scope."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        scope: TenantScope,
        document_store: DocumentStore,
        event_publisher: EventPublisher,
    ) -> None:
        self._scope = scope
        self._documents = DocumentRepository(session, scope)
        self._chunks = DocumentChunkRepository(session, scope)
        self._document_store = document_store
        self._events = event_publisher

    async def process_document(self, document_id: UUID) -> None:
        document = await self._documents.get(document_id)
        if document is None:
            raise NotFoundError()

        if document.processing_status is not ProcessingStatus.EXTRACTING:
            raise ConflictError("The document is not ready for extraction.")

        if document.content_type != "text/plain":
            document.processing_status = ProcessingStatus.QUARANTINED
            document.processing_error = f"Unsupported content type: {document.content_type}"

            await self._events.publish(
                DomainEvent(
                    event_type="DocumentQuarantined",
                    organization_id=str(self._scope.organization_id),
                    payload={
                        "document_id": str(document.id),
                        "content_type": document.content_type,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )
            return

        try:
            body = await self._document_store.get_object(key=document.storage_key)
            text = body.decode("utf-8")

            document.processing_status = ProcessingStatus.INDEXING

            chunks = self._build_text_chunks(
                document=document,
                text=text,
            )
            await self._chunks.add_many(chunks)

            document.processing_status = ProcessingStatus.READY
            document.processing_error = None

            await self._events.publish(
                DomainEvent(
                    event_type="DocumentIndexed",
                    organization_id=str(self._scope.organization_id),
                    payload={
                        "document_id": str(document.id),
                        "chunk_count": len(chunks),
                    },
                    occurred_at=datetime.now(UTC),
                )
            )

        except UnicodeDecodeError as exc:
            document.processing_status = ProcessingStatus.FAILED
            document.processing_error = "The document is not valid UTF-8 text."

            await self._events.publish(
                DomainEvent(
                    event_type="DocumentProcessingFailed",
                    organization_id=str(self._scope.organization_id),
                    payload={
                        "document_id": str(document.id),
                        "reason": document.processing_error,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )

            raise ConflictError(document.processing_error) from exc

    def _build_text_chunks(
        self,
        *,
        document: Document,
        text: str,
    ) -> list_type[DocumentChunk]:
        normalized = text.strip()
        if not normalized:
            return []

        pieces = [
            normalized[index : index + TEXT_CHUNK_SIZE]
            for index in range(0, len(normalized), TEXT_CHUNK_SIZE)
        ]

        return [
            DocumentChunk(
                organization_id=self._scope.organization_id,
                document_id=document.id,
                chunk_index=index,
                content=content,
                token_count=max(1, len(content.split())),
                chunk_metadata={
                    "content_type": document.content_type,
                    "filename": document.filename,
                },
            )
            for index, content in enumerate(pieces)
        ]
