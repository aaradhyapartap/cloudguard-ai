"""Port: persistence operations required by document processing."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.models.documents import ProcessingChunk, ProcessingDocument
from app.models.enums import ProcessingStatus


class DocumentProcessingRepository(ABC):
    """Persistence boundary for the ingestion worker."""

    @abstractmethod
    async def get_document(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
    ) -> ProcessingDocument | None:
        """Fetch one document inside a tenant boundary."""

    @abstractmethod
    async def set_status(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        status: ProcessingStatus,
        error: str | None,
    ) -> None:
        """Update document processing state."""

    @abstractmethod
    async def add_chunks(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        chunks: list[ProcessingChunk],
    ) -> None:
        """Persist ordered chunks for one document."""
