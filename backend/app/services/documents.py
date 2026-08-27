"""Application service for tenant-scoped document ingestion."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.ai import DomainEvent
from app.models.documents import (
    DocumentCreateRequest,
    DocumentResponse,
    DocumentUploadResponse,
)
from app.models.principal import Principal
from app.ports.document_store import DocumentStore
from app.ports.event_publisher import EventPublisher
from app.repositories.documents import DocumentRepository
from app.repositories.tables import Document

UPLOAD_URL_TTL_SECONDS = 900


class DocumentService:
    """Coordinates document metadata, object storage, and ingestion events."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        principal: Principal,
        document_store: DocumentStore,
        event_publisher: EventPublisher,
    ) -> None:
        self._principal = principal
        self._documents = DocumentRepository(session, principal)
        self._document_store = document_store
        self._events = event_publisher

    async def register_upload(
        self,
        payload: DocumentCreateRequest,
    ) -> DocumentUploadResponse:
        document = Document(
            organization_id=self._principal.organization_id,
            filename=payload.filename,
            storage_key="",
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
            document_type=payload.document_type,
            confidentiality_level=payload.confidentiality_level,
            uploader_id=self._principal.user_id,
            department=payload.department or self._principal.department,
            source=payload.source,
            tags=payload.tags,
        )

        await self._documents.add(document)

        storage_key = self._document_store.build_key(
            organization_id=self._principal.organization_id,
            document_id=document.id,
            filename=payload.filename,
        )
        document.storage_key = storage_key

        upload_url = await self._document_store.generate_upload_url(
            organization_id=self._principal.organization_id,
            document_id=document.id,
            filename=payload.filename,
            content_type=payload.content_type,
            expires_in_seconds=UPLOAD_URL_TTL_SECONDS,
        )

        await self._events.publish(
            DomainEvent(
                event_type="DocumentUploadRegistered",
                organization_id=str(self._principal.organization_id),
                payload={
                    "document_id": str(document.id),
                    "storage_key": storage_key,
                    "content_type": payload.content_type,
                    "size_bytes": payload.size_bytes,
                },
                occurred_at=datetime.now(UTC),
            )
        )

        return DocumentUploadResponse(
            document=self._to_response(document),
            upload_url=upload_url,
            expires_in_seconds=UPLOAD_URL_TTL_SECONDS,
        )

    async def get(self, document_id: UUID) -> DocumentResponse:
        document = await self._documents.get(document_id)
        if document is None:
            raise NotFoundError()
        return self._to_response(document)

    async def list(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> list[DocumentResponse]:
        documents = await self._documents.list_page(limit=limit, offset=offset)
        return [self._to_response(document) for document in documents]

    async def count(self) -> int:
        return await self._documents.count()

    @staticmethod
    def _to_response(document: Document) -> DocumentResponse:
        return DocumentResponse(
            id=document.id,
            filename=document.filename,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
            document_type=document.document_type,
            confidentiality_level=document.confidentiality_level,
            processing_status=document.processing_status,
            processing_error=document.processing_error,
            uploader_id=document.uploader_id,
            department=document.department,
            source=document.source,
            tags=list(document.tags),
            created_at=document.created_at,
            updated_at=document.updated_at,
        )
