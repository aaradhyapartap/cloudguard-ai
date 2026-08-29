"""Focused tests for Phase 3 document processing."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.adapters.mock.document_store import InMemoryDocumentStore
from app.adapters.mock.event_publisher import InMemoryEventPublisher
from app.core.errors import ConflictError
from app.models.documents import ProcessingChunk, ProcessingDocument
from app.models.enums import ProcessingStatus
from app.models.tenant import TenantScope
from app.ports.document_processing_repository import DocumentProcessingRepository
from app.services.document_processing import DocumentProcessingService

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class FakeDocumentProcessingRepository(DocumentProcessingRepository):
    def __init__(self, document: ProcessingDocument) -> None:
        self.document = document
        self.processing_error: str | None = None
        self.chunks: list[ProcessingChunk] = []

    async def get_document(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
    ) -> ProcessingDocument | None:
        if organization_id != self.document.organization_id:
            return None
        if document_id != self.document.id:
            return None
        return self.document

    async def set_status(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        status: ProcessingStatus,
        error: str | None,
    ) -> None:
        if organization_id != self.document.organization_id:
            return
        if document_id != self.document.id:
            return

        self.document = self.document.model_copy(update={"processing_status": status})
        self.processing_error = error

    async def add_chunks(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        chunks: list[ProcessingChunk],
    ) -> None:
        if organization_id != self.document.organization_id:
            return
        if document_id != self.document.id:
            return
        self.chunks.extend(chunks)


def make_scope() -> TenantScope:
    return TenantScope(organization_id=ORG_ID)


def make_document(
    *,
    content_type: str = "text/plain",
    status: ProcessingStatus = ProcessingStatus.EXTRACTING,
) -> ProcessingDocument:
    return ProcessingDocument(
        id=DOCUMENT_ID,
        organization_id=ORG_ID,
        filename="policy.txt",
        storage_key=f"org/{ORG_ID}/documents/{DOCUMENT_ID}/policy.txt",
        content_type=content_type,
        processing_status=status,
    )


def make_service(
    document: ProcessingDocument,
) -> tuple[
    DocumentProcessingService,
    InMemoryDocumentStore,
    InMemoryEventPublisher,
    FakeDocumentProcessingRepository,
]:
    store = InMemoryDocumentStore()
    events = InMemoryEventPublisher()
    repository = FakeDocumentProcessingRepository(document)

    service = DocumentProcessingService(
        scope=make_scope(),
        repository=repository,
        document_store=store,
        event_publisher=events,
    )

    return service, store, events, repository


async def test_process_text_document_creates_ordered_chunks() -> None:
    document = make_document()
    service, store, events, repository = make_service(document)

    body = ("A" * 1000 + "B" * 1000 + "C" * 100).encode()
    await store.put_object(
        key=document.storage_key,
        body=body,
        content_type="text/plain",
    )

    await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.READY
    assert [chunk.chunk_index for chunk in repository.chunks] == [0, 1, 2]
    assert [len(chunk.content) for chunk in repository.chunks] == [1000, 1000, 100]
    assert [event.event_type for event in events.events] == ["DocumentIndexed"]


async def test_process_document_rejects_wrong_starting_status() -> None:
    document = make_document(status=ProcessingStatus.QUEUED)
    service, _, _, _ = make_service(document)

    with pytest.raises(ConflictError):
        await service.process_document(DOCUMENT_ID)


async def test_process_document_quarantines_unsupported_content_type() -> None:
    document = make_document(content_type="application/pdf")
    service, _, events, repository = make_service(document)

    await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.QUARANTINED
    assert "Unsupported content type" in (repository.processing_error or "")
    assert repository.chunks == []
    assert [event.event_type for event in events.events] == ["DocumentQuarantined"]


async def test_process_document_marks_invalid_utf8_as_failed() -> None:
    document = make_document()
    service, store, events, repository = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=b"\xff\xfe\xfa",
        content_type="text/plain",
    )

    with pytest.raises(ConflictError):
        await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.FAILED
    assert repository.processing_error == "The document is not valid UTF-8 text."
    assert repository.chunks == []
    assert [event.event_type for event in events.events] == ["DocumentProcessingFailed"]
