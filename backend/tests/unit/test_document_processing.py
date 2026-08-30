"""Focused tests for Phase 3 document processing."""

from __future__ import annotations

import io
from uuid import UUID

import pypdf
import pytest
from app.adapters.mock.document_store import InMemoryDocumentStore
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.adapters.mock.event_publisher import InMemoryEventPublisher
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.core.errors import ConflictError, UpstreamError
from app.models.ai import EmbeddingResult, VectorRecord
from app.models.documents import ProcessingChunk, ProcessingDocument
from app.models.enums import ConfidentialityLevel, ProcessingStatus
from app.models.tenant import TenantScope
from app.ports.document_processing_repository import DocumentProcessingRepository
from app.ports.document_store import DocumentStore
from app.ports.llm_provider import EmbeddingProvider
from app.ports.vector_store import VectorStore
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
        font_ref = writer._add_object(font_dict)

        fonts: DictionaryObject = DictionaryObject()
        fonts[NameObject("/F1")] = font_ref

        font_resources: DictionaryObject = DictionaryObject()
        font_resources[NameObject("/Font")] = fonts

        page_resources = writer._add_object(font_resources)
        page[NameObject("/Resources")] = page_resources
        page[NameObject("/Contents")] = writer._add_object(stream_obj)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


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
# In-memory test doubles
# ---------------------------------------------------------------------------


class FakeDocumentProcessingRepository(DocumentProcessingRepository):
    def __init__(self, document: ProcessingDocument) -> None:
        self.document = document
        self.status_history: list[ProcessingStatus] = [document.processing_status]
        self.chunks: list[ProcessingChunk] = []
        self.processing_error: str | None = None

    async def claim_for_processing(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
    ) -> bool:
        if (
            self.document.organization_id == organization_id
            and self.document.id == document_id
            and self.document.processing_status is ProcessingStatus.QUEUED
        ):
            self.document = self.document.model_copy(
                update={"processing_status": ProcessingStatus.EXTRACTING}
            )
            self.status_history.append(ProcessingStatus.EXTRACTING)
            return True
        return False

    async def get_document(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
    ) -> ProcessingDocument | None:
        if (
            self.document.organization_id == organization_id
            and self.document.id == document_id
        ):
            return self.document
        return None

    async def set_status(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        status: ProcessingStatus,
        error: str | None,
    ) -> None:
        if (
            self.document.organization_id == organization_id
            and self.document.id == document_id
        ):
            self.document = self.document.model_copy(
                update={"processing_status": status}
            )
            self.status_history.append(status)
            self.processing_error = error

    async def add_chunks(
        self,
        *,
        organization_id: UUID,
        document_id: UUID,
        chunks: list[ProcessingChunk],
    ) -> None:
        if (
            self.document.organization_id == organization_id
            and self.document.id == document_id
        ):
            self.chunks = list(chunks)


def make_scope() -> TenantScope:
    return TenantScope(organization_id=ORG_ID)


def make_document(
    *,
    content_type: str = "text/plain",
    status: ProcessingStatus = ProcessingStatus.QUEUED,
    filename: str = "policy.txt",
    confidentiality_level: ConfidentialityLevel = ConfidentialityLevel.INTERNAL,
) -> ProcessingDocument:
    return ProcessingDocument(
        id=DOCUMENT_ID,
        organization_id=ORG_ID,
        filename=filename,
        storage_key=f"org/{ORG_ID}/documents/{DOCUMENT_ID}/{filename}",
        content_type=content_type,
        processing_status=status,
        confidentiality_level=confidentiality_level,
    )


def make_service(
    document: ProcessingDocument,
    *,
    vector_store: VectorStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> tuple[
    DocumentProcessingService,
    InMemoryDocumentStore,
    InMemoryEventPublisher,
    FakeDocumentProcessingRepository,
]:
    store = InMemoryDocumentStore()
    events = InMemoryEventPublisher()
    repository = FakeDocumentProcessingRepository(document)
    vectors = vector_store or InMemoryVectorStore()
    embeddings = embedding_provider or MockEmbeddingProvider()

    service = DocumentProcessingService(
        scope=make_scope(),
        repository=repository,
        document_store=store,
        event_publisher=events,
        vector_store=vectors,
        embedding_provider=embeddings,
    )

    return service, store, events, repository


# ---------------------------------------------------------------------------
# Atomic claim & status lifecycle
# ---------------------------------------------------------------------------


async def test_atomic_claim_succeeds_from_queued() -> None:
    """claim_for_processing on a QUEUED document must return True and transition to EXTRACTING."""
    document = make_document(status=ProcessingStatus.QUEUED)
    repository = FakeDocumentProcessingRepository(document)

    claimed = await repository.claim_for_processing(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
    )

    assert claimed is True
    assert repository.document.processing_status is ProcessingStatus.EXTRACTING


async def test_atomic_claim_second_claim_returns_false() -> None:
    """A second claim attempt on the same document must return False (exactly one worker wins)."""
    document = make_document(status=ProcessingStatus.QUEUED)
    repository = FakeDocumentProcessingRepository(document)

    first_claim = await repository.claim_for_processing(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
    )
    second_claim = await repository.claim_for_processing(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
    )

    assert first_claim is True
    assert second_claim is False
    assert repository.document.processing_status is ProcessingStatus.EXTRACTING


@pytest.mark.parametrize(
    "invalid_status",
    [
        ProcessingStatus.EXTRACTING,
        ProcessingStatus.INDEXING,
        ProcessingStatus.READY,
        ProcessingStatus.FAILED,
        ProcessingStatus.QUARANTINED,
    ],
)
async def test_atomic_claim_fails_for_non_queued_statuses(
    invalid_status: ProcessingStatus,
) -> None:
    """claim_for_processing must return False for any document not in QUEUED status."""
    document = make_document(status=invalid_status)
    repository = FakeDocumentProcessingRepository(document)

    claimed = await repository.claim_for_processing(
        organization_id=ORG_ID,
        document_id=DOCUMENT_ID,
    )

    assert claimed is False
    assert repository.document.processing_status is invalid_status


async def test_service_does_not_extract_or_add_chunks_when_claim_is_lost() -> None:
    """When a worker loses the atomic claim (e.g. document already claimed/processed),
    process_document raises ConflictError without reading storage, adding chunks,
    or emitting events.
    """
    document = make_document(status=ProcessingStatus.EXTRACTING)
    service, store, events, repository = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=b"Unused body",
        content_type="text/plain",
    )

    with pytest.raises(ConflictError, match="not ready for extraction"):
        await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.EXTRACTING
    assert repository.chunks == []
    assert events.events == []


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


async def test_process_document_from_queued_status_succeeds() -> None:
    """A document starting from QUEUED (direct S3 upload) must reach READY."""
    document = make_document(status=ProcessingStatus.QUEUED)
    service, store, events, repository = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=b"Direct S3 upload policy text content.",
        content_type="text/plain",
    )

    await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.READY
    assert len(repository.chunks) >= 1
    assert [event.event_type for event in events.events] == ["DocumentIndexed"]


@pytest.mark.parametrize(
    "invalid_status",
    [
        ProcessingStatus.READY,
        ProcessingStatus.INDEXING,
        ProcessingStatus.FAILED,
        ProcessingStatus.QUARANTINED,
    ],
)
async def test_process_document_rejects_terminal_or_in_progress_statuses(
    invalid_status: ProcessingStatus,
) -> None:
    """Duplicate/replayed events on non-extractable documents must raise ConflictError
    and must not modify the document, insert chunks, or emit events.
    """
    document = make_document(status=invalid_status)
    service, store, events, repository = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=b"Some content",
        content_type="text/plain",
    )

    with pytest.raises(ConflictError, match="not ready for extraction"):
        await service.process_document(DOCUMENT_ID)

    # Document status and chunks must remain completely untouched.
    assert repository.document.processing_status is invalid_status
    assert repository.chunks == []
    assert events.events == []


async def test_process_empty_text_document_marks_as_failed() -> None:
    """A 0-byte text document must transition to FAILED and never reach READY."""
    document = make_document(content_type="text/plain", filename="empty.txt")
    service, store, events, repository = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=b"",
        content_type="text/plain",
    )

    with pytest.raises(ConflictError):
        await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.FAILED
    assert repository.processing_error == "The document contains no extractable text."
    assert repository.chunks == []
    assert [event.event_type for event in events.events] == [
        "DocumentProcessingFailed"
    ]


async def test_process_whitespace_only_text_document_marks_as_failed() -> None:
    """A whitespace-only text document must transition to FAILED and never reach READY."""
    document = make_document(content_type="text/plain", filename="blank.txt")
    service, store, events, repository = make_service(document)

    await store.put_object(
        key=document.storage_key,
        body=b"   \n\t  \r\n   ",
        content_type="text/plain",
    )

    with pytest.raises(ConflictError):
        await service.process_document(DOCUMENT_ID)

    assert repository.document.processing_status is ProcessingStatus.FAILED
    assert repository.processing_error == "The document contains no extractable text."
    assert repository.chunks == []
    assert [event.event_type for event in events.events] == [
        "DocumentProcessingFailed"
    ]


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
        vector_store=InMemoryVectorStore(),
        embedding_provider=MockEmbeddingProvider(),
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


# ---------------------------------------------------------------------------
# Phase 4.2 Embedding & Vector Ingestion Lifecycle Tests
# ---------------------------------------------------------------------------


class _FailingEmbeddingProvider(EmbeddingProvider):
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        raise UpstreamError("Bedrock unavailable")

    @property
    def embedding_model_id(self) -> str:
        return "mock:failing"


class _MismatchedCountEmbeddingProvider(EmbeddingProvider):
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        # Return fewer vectors than texts
        return EmbeddingResult(
            vectors=[[0.01] * 1024] if texts else [],
            model_id="mock:mismatched",
            dimensions=1024,
            input_tokens=10,
        )

    @property
    def embedding_model_id(self) -> str:
        return "mock:mismatched"


class _FailingVectorStore(VectorStore):
    async def upsert(self, records: list[VectorRecord]) -> int:
        raise UpstreamError("Vector database unavailable")

    async def search(self, **kwargs: object) -> list[object]:
        return []

    async def delete_by_document(self, **kwargs: object) -> int:
        return 0


class _PartialCountVectorStore(VectorStore):
    async def upsert(self, records: list[VectorRecord]) -> int:
        return max(0, len(records) - 1)  # Upsert 1 fewer than expected

    async def search(self, **kwargs: object) -> list[object]:
        return []

    async def delete_by_document(self, **kwargs: object) -> int:
        return 0


async def test_process_document_success_persists_embeddings_and_marks_ready() -> None:
    """Document reaching READY must have generated embeddings and persisted vectors."""
    doc = make_document(
        content_type="text/plain",
        confidentiality_level=ConfidentialityLevel.INTERNAL,
    )
    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider()

    service, store, events, repo = make_service(
        doc,
        vector_store=vector_store,
        embedding_provider=embedding_provider,
    )

    await store.put_object(
        key=doc.storage_key,
        body=b"Security policy section 1.\nSecurity policy section 2.",
        content_type="text/plain",
    )

    await service.process_document(doc.id)

    # Document must reach READY
    assert repo.document.processing_status is ProcessingStatus.READY
    assert repo.processing_error is None
    assert len(repo.chunks) == 1

    # Vectors must be persisted in vector_store
    matches = await vector_store.search(
        embedding=[0.0] * 1024,
        organization_id=ORG_ID,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
        top_k=10,
    )
    assert len(matches) == 1
    assert matches[0].document_id == str(doc.id)
    assert matches[0].chunk_id == str(repo.chunks[0].id)

    # Event DocumentIndexed must be published
    event_types = [e.event_type for e in events.events]
    assert event_types == ["DocumentIndexed"]
    assert events.events[0].payload["chunk_count"] == 1


async def test_process_pdf_document_success_persists_embeddings_and_marks_ready() -> None:
    """PDF document reaching READY must generate embeddings and persist vectors."""
    doc = make_document(
        content_type="application/pdf",
        filename="cloud_policy.pdf",
    )
    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider()

    service, store, _events, repo = make_service(
        doc,
        vector_store=vector_store,
        embedding_provider=embedding_provider,
    )

    pdf_bytes = _make_text_pdf("Page 1 cloud architecture policy", "Page 2 tenant data isolation")
    await store.put_object(
        key=doc.storage_key,
        body=pdf_bytes,
        content_type="application/pdf",
    )

    await service.process_document(doc.id)

    assert repo.document.processing_status is ProcessingStatus.READY
    assert len(repo.chunks) == 1

    matches = await vector_store.search(
        embedding=[0.0] * 1024,
        organization_id=ORG_ID,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
        top_k=10,
    )
    assert len(matches) == 1
    assert matches[0].document_id == str(doc.id)


async def test_embedding_failure_transitions_to_failed() -> None:
    """Embedding failure transitions document to FAILED and publishes DocumentProcessingFailed."""
    doc = make_document()
    vector_store = InMemoryVectorStore()
    failing_embeddings = _FailingEmbeddingProvider()

    service, store, events, repo = make_service(
        doc,
        vector_store=vector_store,
        embedding_provider=failing_embeddings,
    )

    await store.put_object(
        key=doc.storage_key,
        body=b"Text to embed",
        content_type="text/plain",
    )

    with pytest.raises(UpstreamError, match=r"The document could not be embedded\."):
        await service.process_document(doc.id)

    assert repo.document.processing_status is ProcessingStatus.FAILED
    assert repo.processing_error == "The document could not be embedded."

    event_types = [e.event_type for e in events.events]
    assert event_types == ["DocumentProcessingFailed"]
    assert events.events[0].payload["reason"] == "The document could not be embedded."


async def test_mismatched_embedding_count_transitions_to_failed() -> None:
    """If embedding provider returns wrong vector count, document transitions to FAILED."""
    doc = make_document()
    vector_store = InMemoryVectorStore()
    mismatched_embeddings = _MismatchedCountEmbeddingProvider()

    service, store, events, repo = make_service(
        doc,
        vector_store=vector_store,
        embedding_provider=mismatched_embeddings,
    )

    # 2500 chars -> 3 chunks
    await store.put_object(
        key=doc.storage_key,
        body=b"A" * 2500,
        content_type="text/plain",
    )

    with pytest.raises(UpstreamError, match=r"The document could not be embedded\."):
        await service.process_document(doc.id)

    assert repo.document.processing_status is ProcessingStatus.FAILED
    assert repo.processing_error == "The document could not be embedded."
    assert not any(e.event_type == "DocumentIndexed" for e in events.events)


async def test_vector_persistence_failure_transitions_to_failed() -> None:
    """VectorStore failure transitions document to FAILED and publishes DocumentProcessingFailed."""
    doc = make_document()
    failing_vectors = _FailingVectorStore()
    embeddings = MockEmbeddingProvider()

    service, store, events, repo = make_service(
        doc,
        vector_store=failing_vectors,
        embedding_provider=embeddings,
    )

    await store.put_object(
        key=doc.storage_key,
        body=b"Text to embed",
        content_type="text/plain",
    )

    with pytest.raises(UpstreamError, match=r"The document vectors could not be saved\."):
        await service.process_document(doc.id)

    assert repo.document.processing_status is ProcessingStatus.FAILED
    assert repo.processing_error == "The document vectors could not be saved."

    event_types = [e.event_type for e in events.events]
    assert event_types == ["DocumentProcessingFailed"]
    assert events.events[0].payload["reason"] == "The document vectors could not be saved."


async def test_mismatched_vector_upsert_count_transitions_to_failed() -> None:
    """If VectorStore.upsert affects fewer rows than expected, document transitions to FAILED."""
    doc = make_document()
    partial_vectors = _PartialCountVectorStore()
    embeddings = MockEmbeddingProvider()

    service, store, events, repo = make_service(
        doc,
        vector_store=partial_vectors,
        embedding_provider=embeddings,
    )

    await store.put_object(
        key=doc.storage_key,
        body=b"Text to embed",
        content_type="text/plain",
    )

    with pytest.raises(UpstreamError, match=r"The document vectors could not be saved\."):
        await service.process_document(doc.id)

    assert repo.document.processing_status is ProcessingStatus.FAILED
    assert repo.processing_error == "The document vectors could not be saved."
    assert not any(e.event_type == "DocumentIndexed" for e in events.events)


async def test_duplicate_invocation_while_extracting_fails_with_conflict() -> None:
    """A concurrent worker invocation while document is EXTRACTING cannot claim it."""
    doc = make_document(status=ProcessingStatus.EXTRACTING)
    service, _store, events, repo = make_service(doc)

    with pytest.raises(ConflictError, match=r"The document is not ready for extraction\."):
        await service.process_document(doc.id)

    # State and chunks must remain completely untouched
    assert repo.document.processing_status is ProcessingStatus.EXTRACTING
    assert repo.chunks == []
    assert events.events == []


async def test_invocation_for_failed_document_does_not_silently_reprocess() -> None:
    """An invocation for a FAILED document must not mutate or reprocess it."""
    doc = make_document(status=ProcessingStatus.FAILED)
    service, _store, events, repo = make_service(doc)

    with pytest.raises(ConflictError, match=r"The document is not ready for extraction\."):
        await service.process_document(doc.id)

    assert repo.document.processing_status is ProcessingStatus.FAILED
    assert repo.chunks == []
    assert events.events == []


async def test_invocation_for_ready_document_does_not_mutate() -> None:
    """An invocation for a READY document must not mutate chunks or state."""
    doc = make_document(status=ProcessingStatus.READY)
    service, _store, events, repo = make_service(doc)

    with pytest.raises(ConflictError, match=r"The document is not ready for extraction\."):
        await service.process_document(doc.id)

    assert repo.document.processing_status is ProcessingStatus.READY
    assert repo.chunks == []
    assert events.events == []


async def test_invocation_for_quarantined_document_does_not_mutate() -> None:
    """An invocation for a QUARANTINED document must not mutate chunks or state."""
    doc = make_document(status=ProcessingStatus.QUARANTINED)
    service, _store, events, repo = make_service(doc)

    with pytest.raises(ConflictError, match=r"The document is not ready for extraction\."):
        await service.process_document(doc.id)

    assert repo.document.processing_status is ProcessingStatus.QUARANTINED
    assert repo.chunks == []
    assert events.events == []


async def test_explicit_reset_to_queued_allows_reclaim_and_processing() -> None:
    """An explicit reset of a FAILED document to QUEUED allows it to be claimed and processed."""
    doc = make_document(status=ProcessingStatus.FAILED)
    vector_store = InMemoryVectorStore()
    embeddings = MockEmbeddingProvider()

    service, store, events, repo = make_service(
        doc,
        vector_store=vector_store,
        embedding_provider=embeddings,
    )

    await store.put_object(
        key=doc.storage_key,
        body=b"Updated policy text for reprocessing.",
        content_type="text/plain",
    )

    # Before reset: invocation fails with ConflictError
    with pytest.raises(ConflictError):
        await service.process_document(doc.id)

    # Deliberate reset operation returning document to QUEUED
    repo.document = repo.document.model_copy(
        update={"processing_status": ProcessingStatus.QUEUED, "processing_error": None}
    )

    # After reset: claim succeeds and document reaches READY
    await service.process_document(doc.id)
    assert repo.document.processing_status is ProcessingStatus.READY
    assert len(repo.chunks) == 1
    assert any(e.event_type == "DocumentIndexed" for e in events.events)
