"""Focused tests for Phase 3 document processing."""

from __future__ import annotations

import io
from uuid import UUID

import pypdf
import pytest
from app.adapters.mock.document_store import InMemoryDocumentStore
from app.adapters.mock.event_publisher import InMemoryEventPublisher
from app.core.errors import ConflictError, UpstreamError
from app.models.documents import ProcessingChunk, ProcessingDocument
from app.models.enums import ProcessingStatus
from app.models.tenant import TenantScope
from app.ports.document_processing_repository import DocumentProcessingRepository
from app.ports.document_store import DocumentStore
from app.services.document_processing import DocumentProcessingService

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


# ---------------------------------------------------------------------------
# PDF fixture helpers
# ---------------------------------------------------------------------------


def _make_text_pdf(*pages: str) -> bytes:
    """Build a valid PDF with one text layer per page using pypdf.PdfWriter.

    Each string in *pages* appears as extractable text on the corresponding
    page.  The resulting bytes can be parsed by pypdf without warnings.
    """
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()

    for text in pages:
        page = writer.add_blank_page(612, 792)

        # Escape chars that would break the PDF string literal.
        escaped = (
            text.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        stream_data = f"BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET".encode()
        stream_obj = DecodedStreamObject()
        stream_obj.set_data(stream_data)

        font_dict: DictionaryObject = DictionaryObject()
        font_dict[NameObject("/Type")] = NameObject("/Font")
        font_dict[NameObject("/Subtype")] = NameObject("/Type1")
        font_dict[NameObject("/BaseFont")] = NameObject("/Helvetica")
        font_ref = writer._add_object(font_dict)  # type: ignore[attr-defined]

        fonts: DictionaryObject = DictionaryObject()
        fonts[NameObject("/F1")] = font_ref

        resources: DictionaryObject = DictionaryObject()
        resources[NameObject("/Font")] = fonts

        content_ref = writer._add_object(stream_obj)  # type: ignore[attr-defined]
        page[NameObject("/Contents")] = content_ref
        page[NameObject("/Resources")] = resources

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_encrypted_pdf() -> bytes:
    """Return a password-protected PDF."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("secret")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_blank_pdf() -> bytes:
    """Return a valid PDF with no extractable text (simulates image-only)."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(200, 200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


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
    filename: str = "policy.txt",
) -> ProcessingDocument:
    return ProcessingDocument(
        id=DOCUMENT_ID,
        organization_id=ORG_ID,
        filename=filename,
        storage_key=f"org/{ORG_ID}/documents/{DOCUMENT_ID}/{filename}",
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


# ---------------------------------------------------------------------------
# text/plain — existing behaviour must be preserved
# ---------------------------------------------------------------------------


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
    # Word documents remain unsupported and must be quarantined.
    document = make_document(content_type="application/msword")
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


# ---------------------------------------------------------------------------
# application/pdf — new behaviour
# ---------------------------------------------------------------------------


async def test_process_pdf_document_reaches_ready() -> None:
    """A normal text-based PDF must reach READY with at least one chunk."""
    document = make_document(content_type="application/pdf", filename="policy.pdf")
    service, store, events, repository = make_service(document)

    pdf_bytes = _make_text_pdf("This is a compliance policy document.")
    await store.put_object(
        key=document.storage_key,
        body=pdf_bytes,
        content_type="application/pdf",
    )

    await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.READY
    assert len(repository.chunks) >= 1
    combined = "".join(c.content for c in repository.chunks)
    assert "compliance" in combined
    assert [event.event_type for event in events.events] == ["DocumentIndexed"]


async def test_process_pdf_preserves_page_order() -> None:
    """Text from each page must appear in page order in the final chunks."""
    document = make_document(content_type="application/pdf", filename="multi.pdf")
    service, store, _events, repository = make_service(document)

    pdf_bytes = _make_text_pdf("First page content", "Second page content", "Third page content")
    await store.put_object(
        key=document.storage_key,
        body=pdf_bytes,
        content_type="application/pdf",
    )

    await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.READY
    combined = "".join(c.content for c in repository.chunks)
    first = combined.index("First")
    second = combined.index("Second")
    third = combined.index("Third")
    assert first < second < third, "Page text must appear in page order"


async def test_process_malformed_pdf_marks_as_failed() -> None:
    """A file with a .pdf content-type but corrupt data must reach FAILED."""
    document = make_document(content_type="application/pdf", filename="corrupt.pdf")
    service, store, events, repository = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=b"This is not a PDF file at all \xff\xfe",
        content_type="application/pdf",
    )

    with pytest.raises(ConflictError):
        await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.FAILED
    assert repository.processing_error == "The PDF could not be read."
    assert repository.chunks == []
    assert [event.event_type for event in events.events] == ["DocumentProcessingFailed"]


async def test_process_encrypted_pdf_quarantines() -> None:
    """An encrypted (password-protected) PDF must be quarantined, not failed."""
    document = make_document(content_type="application/pdf", filename="secret.pdf")
    service, store, events, repository = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=_make_encrypted_pdf(),
        content_type="application/pdf",
    )

    # Must NOT raise — quarantine is a controlled outcome, not an exception.
    await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.QUARANTINED
    assert "encrypted" in (repository.processing_error or "").lower()
    assert repository.chunks == []
    assert [event.event_type for event in events.events] == ["DocumentQuarantined"]


async def test_process_image_only_pdf_marks_as_failed() -> None:
    """A PDF with no extractable text (image-only/blank) must reach FAILED.

    It must never silently reach READY with zero content.
    """
    document = make_document(content_type="application/pdf", filename="scanned.pdf")
    service, store, events, repository = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=_make_blank_pdf(),
        content_type="application/pdf",
    )

    with pytest.raises(ConflictError):
        await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.FAILED
    assert repository.processing_error is not None
    assert "no extractable text" in (repository.processing_error or "").lower()
    assert repository.chunks == []
    assert [event.event_type for event in events.events] == ["DocumentProcessingFailed"]


# ---------------------------------------------------------------------------
# Storage / provider failure
# ---------------------------------------------------------------------------


class _FailingDocumentStore(DocumentStore):
    """A DocumentStore stub that always raises UpstreamError on get_object."""

    from uuid import UUID as _UUID

    async def generate_upload_url(
        self,
        *,
        organization_id: _UUID,
        document_id: _UUID,
        filename: str,
        content_type: str,
        expires_in_seconds: int = 900,
    ) -> str:  # pragma: no cover
        raise NotImplementedError

    async def get_object(self, *, key: str) -> bytes:
        raise UpstreamError("S3 unavailable")

    async def put_object(
        self, *, key: str, body: bytes, content_type: str
    ) -> None:  # pragma: no cover
        raise NotImplementedError

    async def head_object(self, *, key: str) -> dict[str, object] | None:  # pragma: no cover
        raise NotImplementedError

    async def delete_object(self, *, key: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def build_key(
        self,
        *,
        organization_id: _UUID,
        document_id: _UUID,
        filename: str,
    ) -> str:  # pragma: no cover
        raise NotImplementedError


async def test_storage_failure_marks_document_as_failed() -> None:
    """UpstreamError from DocumentStore.get_object must transition the document
    to FAILED with a stable, non-sensitive processing_error and publish
    DocumentProcessingFailed before propagating an UpstreamError.
    """
    document = make_document()  # text/plain — failure is storage-layer, not format
    events = InMemoryEventPublisher()
    repository = FakeDocumentProcessingRepository(document)

    service = DocumentProcessingService(
        scope=make_scope(),
        repository=repository,
        document_store=_FailingDocumentStore(),
        event_publisher=events,
    )

    with pytest.raises(UpstreamError):
        await service.process_document(DOCUMENT_ID)

    # Document must reach FAILED — never stuck in EXTRACTING.
    assert repository.document.processing_status is ProcessingStatus.FAILED

    # Error message must be stable and must not expose provider internals.
    assert repository.processing_error == "The document could not be retrieved from storage."

    # No chunks must have been created.
    assert repository.chunks == []

    # DocumentProcessingFailed must be published.
    assert [event.event_type for event in events.events] == ["DocumentProcessingFailed"]
    event_payload = events.events[0].payload
    # Payload reason must also be stable/non-sensitive.
    assert event_payload["reason"] == "The document could not be retrieved from storage."
