"""Focused tests for Phase 3 document upload completion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.adapters.mock.document_store import InMemoryDocumentStore
from app.adapters.mock.event_publisher import InMemoryEventPublisher
from app.core.errors import ConflictError, NotFoundError
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
    def __init__(self, document: Document | None) -> None:
        self.document = document

    async def get(self, document_id: UUID) -> Document | None:
        if self.document is None or self.document.id != document_id:
            return None
        return self.document


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
    status: ProcessingStatus = ProcessingStatus.QUEUED,
) -> Document:
    now = datetime.now(UTC)

    return Document(
        id=DOCUMENT_ID,
        organization_id=ORG_ID,
        filename="policy.txt",
        storage_key=f"org/{ORG_ID}/documents/{DOCUMENT_ID}/policy.txt",
        content_type="text/plain",
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
    document: Document | None,
    *,
    store: InMemoryDocumentStore | None = None,
) -> tuple[DocumentService, InMemoryDocumentStore, InMemoryEventPublisher]:
    document_store = store or InMemoryDocumentStore()
    events = InMemoryEventPublisher()

    service = DocumentService(
        session=Any,  # type: ignore[arg-type]
        principal=make_principal(),
        document_store=document_store,
        event_publisher=events,
    )
    service._documents = FakeDocumentRepository(document)  # type: ignore[assignment]

    return service, document_store, events


async def test_complete_upload_moves_document_to_extracting() -> None:
    document = make_document()
    service, store, events = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=b"policy text",
        content_type="text/plain",
    )

    response = await service.complete_upload(DOCUMENT_ID)

    assert response.processing_status is ProcessingStatus.EXTRACTING
    assert document.processing_status is ProcessingStatus.EXTRACTING
    assert document.processing_error is None
    assert [event.event_type for event in events.events] == [
        "DocumentUploadCompleted"
    ]
    assert events.events[0].payload["document_id"] == str(DOCUMENT_ID)


async def test_complete_upload_fails_when_object_is_missing() -> None:
    document = make_document()
    service, _, events = make_service(document)

    with pytest.raises(NotFoundError):
        await service.complete_upload(DOCUMENT_ID)

    assert document.processing_status is ProcessingStatus.QUEUED
    assert events.events == []


async def test_complete_upload_rejects_duplicate_completion() -> None:
    document = make_document(status=ProcessingStatus.EXTRACTING)
    service, store, events = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=b"policy text",
        content_type="text/plain",
    )

    with pytest.raises(ConflictError):
        await service.complete_upload(DOCUMENT_ID)

    assert document.processing_status is ProcessingStatus.EXTRACTING
    assert events.events == []


async def test_complete_upload_returns_not_found_for_unknown_document() -> None:
    service, _, events = make_service(None)

    with pytest.raises(NotFoundError):
        await service.complete_upload(DOCUMENT_ID)

    assert events.events == []
