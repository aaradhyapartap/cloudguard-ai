"""Focused tests for Phase 3 document processing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.adapters.mock.document_store import InMemoryDocumentStore
from app.adapters.mock.event_publisher import InMemoryEventPublisher
from app.core.errors import ConflictError
from app.models.enums import (
    ConfidentialityLevel,
    DocumentType,
    ProcessingStatus,
    Role,
)
from app.models.principal import Principal
from app.repositories.tables import Document
from app.services.documents import DocumentService

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class FakeDocumentRepository:
    def __init__(self, document: Document) -> None:
        self.document = document

    async def get(self, document_id: UUID) -> Document | None:
        if self.document.id != document_id:
            return None
        return self.document


class FakeChunkRepository:
    def __init__(self) -> None:
        self.chunks: list[Any] = []

    async def add_many(self, chunks: list[Any]) -> list[Any]:
        self.chunks.extend(chunks)
        return chunks


def make_principal() -> Principal:
    return Principal(
        user_id=USER_ID,
        organization_id=ORG_ID,
        role=Role.ANALYST,
        email="analyst@acme.test",
        department="Finance",
    )


def make_document(
    *,
    content_type: str = "text/plain",
    status: ProcessingStatus = ProcessingStatus.EXTRACTING,
) -> Document:
    now = datetime.now(UTC)

    return Document(
        id=DOCUMENT_ID,
        organization_id=ORG_ID,
        filename="policy.txt",
        storage_key=f"org/{ORG_ID}/documents/{DOCUMENT_ID}/policy.txt",
        content_type=content_type,
        size_bytes=12,
        document_type=DocumentType.POLICY,
        confidentiality_level=ConfidentialityLevel.INTERNAL,
        processing_status=status,
        processing_error=None,
        uploader_id=USER_ID,
        department="Finance",
        source="unit-test",
        tags=["phase3"],
        created_at=now,
        updated_at=now,
    )


def make_service(
    document: Document,
) -> tuple[
    DocumentService,
    InMemoryDocumentStore,
    InMemoryEventPublisher,
    FakeChunkRepository,
]:
    store = InMemoryDocumentStore()
    events = InMemoryEventPublisher()
    chunks = FakeChunkRepository()

    service = DocumentService(
        session=Any,  # type: ignore[arg-type]
        principal=make_principal(),
        document_store=store,
        event_publisher=events,
    )
    service._documents = FakeDocumentRepository(document)  # type: ignore[assignment]
    service._chunks = chunks  # type: ignore[assignment]

    return service, store, events, chunks


async def test_process_text_document_creates_ordered_chunks() -> None:
    document = make_document()
    service, store, events, chunks = make_service(document)

    body = ("A" * 1000 + "B" * 1000 + "C" * 100).encode()
    await store.put_object(
        key=document.storage_key,
        body=body,
        content_type="text/plain",
    )

    response = await service.process_document(DOCUMENT_ID)

    assert response.processing_status is ProcessingStatus.READY
    assert [chunk.chunk_index for chunk in chunks.chunks] == [0, 1, 2]
    assert [len(chunk.content) for chunk in chunks.chunks] == [1000, 1000, 100]
    assert [event.event_type for event in events.events] == ["DocumentIndexed"]


async def test_process_document_rejects_wrong_starting_status() -> None:
    document = make_document(status=ProcessingStatus.QUEUED)
    service, _, _, _ = make_service(document)

    with pytest.raises(ConflictError):
        await service.process_document(DOCUMENT_ID)


async def test_process_document_quarantines_unsupported_content_type() -> None:
    document = make_document(content_type="application/pdf")
    service, _, events, chunks = make_service(document)

    response = await service.process_document(DOCUMENT_ID)

    assert response.processing_status is ProcessingStatus.QUARANTINED
    assert "Unsupported content type" in (response.processing_error or "")
    assert chunks.chunks == []
    assert [event.event_type for event in events.events] == ["DocumentQuarantined"]


async def test_process_document_marks_invalid_utf8_as_failed() -> None:
    document = make_document()
    service, store, events, chunks = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=b"\xff\xfe\xfa",
        content_type="text/plain",
    )

    with pytest.raises(ConflictError):
        await service.process_document(DOCUMENT_ID)

    assert document.processing_status is ProcessingStatus.FAILED
    assert document.processing_error == "The document is not valid UTF-8 text."
    assert chunks.chunks == []
    assert [event.event_type for event in events.events] == [
        "DocumentProcessingFailed"
    ]
