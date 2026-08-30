"""Internal document extraction and chunking service."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import ConflictError, NotFoundError, UpstreamError
from app.models.ai import DomainEvent
from app.models.documents import ProcessingChunk, ProcessingDocument
from app.models.enums import ProcessingStatus
from app.models.tenant import TenantScope
from app.ports.document_processing_repository import DocumentProcessingRepository
from app.ports.document_store import DocumentStore
from app.ports.event_publisher import EventPublisher

TEXT_CHUNK_SIZE = 1000

_SUPPORTED_CONTENT_TYPES = {"text/plain", "application/pdf"}


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

        if document.content_type not in _SUPPORTED_CONTENT_TYPES:
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

        # Fetch document body. Storage/provider failures are caught here and
        # transitioned to FAILED so the document never stays stuck in EXTRACTING.
        _storage_failure_msg = "The document could not be retrieved from storage."
        try:
            body = await self._document_store.get_object(key=document.storage_key)
        except UpstreamError as exc:
            await self._repository.set_status(
                organization_id=organization_id,
                document_id=document.id,
                status=ProcessingStatus.FAILED,
                error=_storage_failure_msg,
            )
            await self._events.publish(
                DomainEvent(
                    event_type="DocumentProcessingFailed",
                    organization_id=str(organization_id),
                    payload={
                        "document_id": str(document.id),
                        "reason": _storage_failure_msg,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )
            raise UpstreamError(_storage_failure_msg) from exc

        try:
            if document.content_type == "application/pdf":
                text, quarantine_reason = self._extract_pdf_text(body)
                if quarantine_reason is not None:
                    # Encrypted PDFs are a security concern — quarantine, not fail.
                    await self._repository.set_status(
                        organization_id=organization_id,
                        document_id=document.id,
                        status=ProcessingStatus.QUARANTINED,
                        error=quarantine_reason,
                    )
                    await self._events.publish(
                        DomainEvent(
                            event_type="DocumentQuarantined",
                            organization_id=str(organization_id),
                            payload={
                                "document_id": str(document.id),
                                "reason": quarantine_reason,
                            },
                            occurred_at=datetime.now(UTC),
                        )
                    )
                    return
            else:
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

        except _PdfProcessingError as exc:
            error = str(exc)

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

    def _extract_pdf_text(self, body: bytes) -> tuple[str, str | None]:
        """Extract plain text from a PDF byte payload.

        Returns ``(text, None)`` on success, or ``("", reason)`` when the
        document must be quarantined (e.g. encrypted PDF).  Raises
        ``_PdfProcessingError`` for malformed/unreadable files.

        Image-only PDFs that yield no extractable text are treated as a
        processing failure so they never silently reach READY without content.
        OCR is not attempted.
        """
        # pypdf is a pure-Python library with no native dependencies — safe to
        # import here inside the service rather than in an adapter because no
        # AWS SDK is involved (see ADR-0013).
        import pypdf  # lazy import keeps the module loadable without the dep
        from pypdf.errors import PdfReadError

        try:
            reader = pypdf.PdfReader(io.BytesIO(body))
        except PdfReadError as exc:
            raise _PdfProcessingError("The PDF could not be read.") from exc
        except Exception as exc:
            raise _PdfProcessingError("The PDF could not be read.") from exc

        if reader.is_encrypted:
            return "", "The PDF is encrypted and cannot be processed."

        pages: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            pages.append(page_text)

        text = "\n".join(pages)

        if not text.strip():
            raise _PdfProcessingError(
                "The PDF contains no extractable text. "
                "It may be image-only (scanned). OCR is not supported."
            )

        return text, None

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


class _PdfProcessingError(Exception):
    """Raised when PDF text extraction fails for a non-encrypted reason."""

    pass
