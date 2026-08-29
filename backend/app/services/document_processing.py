"""Internal document extraction and chunking service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import ConflictError, NotFoundError
from app.models.ai import DomainEvent
from app.models.documents import ProcessingChunk, ProcessingDocument
from app.models.enums import ProcessingStatus
from app.models.tenant import TenantScope
from app.ports.document_processing_repository import DocumentProcessingRepository
from app.ports.document_store import DocumentStore
from app.ports.event_publisher import EventPublisher

TEXT_CHUNK_SIZE = 1000


class DocumentProcessingService:
    """Processes uploaded documents inside an explicit tenant scope."""

    def __init__(
        self,
        *,
        scope: TenantScope,
        repository: DocumentProcessingRepository,
        document_store: DocumentStore,
        event_publisher: EventPublisher,
    ) -> None:
        self._scope = scope
        self._repository = repository
        self._document_store = document_store
        self._events = event_publisher

    async def process_document(self, document_id: UUID) -> None:
        organization_id = self._scope.organization_id

        document = await self._repository.get_document(
            organization_id=organization_id,
            document_id=document_id,
        )
        if document is None:
            raise NotFoundError()

        if document.processing_status is not ProcessingStatus.EXTRACTING:
            raise ConflictError("The document is not ready for extraction.")

        if document.content_type != "text/plain":
            error = f"Unsupported content type: {document.content_type}"

            await self._repository.set_status(
                organization_id=organization_id,
                document_id=document.id,
                status=ProcessingStatus.QUARANTINED,
                error=error,
            )

            await self._events.publish(
                DomainEvent(
                    event_type="DocumentQuarantined",
                    organization_id=str(organization_id),
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

            await self._repository.set_status(
                organization_id=organization_id,
                document_id=document.id,
                status=ProcessingStatus.INDEXING,
                error=None,
            )

            chunks = self._build_text_chunks(
                document=document,
                text=text,
            )

            await self._repository.add_chunks(
                organization_id=organization_id,
                document_id=document.id,
                chunks=chunks,
            )

            await self._repository.set_status(
                organization_id=organization_id,
                document_id=document.id,
                status=ProcessingStatus.READY,
                error=None,
            )

            await self._events.publish(
                DomainEvent(
                    event_type="DocumentIndexed",
                    organization_id=str(organization_id),
                    payload={
                        "document_id": str(document.id),
                        "chunk_count": len(chunks),
                    },
                    occurred_at=datetime.now(UTC),
                )
            )

        except UnicodeDecodeError as exc:
            error = "The document is not valid UTF-8 text."

            await self._repository.set_status(
                organization_id=organization_id,
                document_id=document.id,
                status=ProcessingStatus.FAILED,
                error=error,
            )

            await self._events.publish(
                DomainEvent(
                    event_type="DocumentProcessingFailed",
                    organization_id=str(organization_id),
                    payload={
                        "document_id": str(document.id),
                        "reason": error,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )

            raise ConflictError(error) from exc

    def _build_text_chunks(
        self,
        *,
        document: ProcessingDocument,
        text: str,
    ) -> list[ProcessingChunk]:
        normalized = text.strip()
        if not normalized:
            return []

        pieces = [
            normalized[index : index + TEXT_CHUNK_SIZE]
            for index in range(0, len(normalized), TEXT_CHUNK_SIZE)
        ]

        return [
            ProcessingChunk(
                chunk_index=index,
                content=content,
                token_count=max(1, len(content.split())),
                metadata={
                    "content_type": document.content_type,
                    "filename": document.filename,
                },
            )
            for index, content in enumerate(pieces)
        ]
