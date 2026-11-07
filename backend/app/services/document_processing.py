"""Internal document extraction, chunking, embedding, and vector indexing service."""

from __future__ import annotations

import io
import time
from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import ConflictError, NotFoundError, UpstreamError
from app.models.ai import DomainEvent, VectorRecord
from app.models.documents import ProcessingChunk, ProcessingDocument
from app.models.enums import ProcessingStatus
from app.models.tenant import TenantScope
from app.ports.document_processing_repository import DocumentProcessingRepository
from app.ports.document_store import DocumentStore
from app.ports.event_publisher import EventPublisher
from app.ports.llm_provider import EmbeddingProvider
from app.ports.telemetry import TelemetryEmitter
from app.ports.vector_store import VectorStore
from app.security.ingestion_limits import (
    MAX_DOCUMENT_CHUNKS,
    MAX_EXTRACTED_CHARACTERS,
    MAX_PDF_PAGES,
    MAX_UPLOAD_BYTES,
    ObjectSizeLimitError,
)
from app.security.sensitive_data import (
    POLICY_VERSION,
    DeterministicSensitiveDataDetector,
    minimize_model_text,
)
from app.telemetry.isolation import safe_telemetry_call

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
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        telemetry: TelemetryEmitter | None = None,
    ) -> None:
        self._scope = scope
        self._repository = repository
        self._document_store = document_store
        self._events = event_publisher
        self._vectors = vector_store
        self._embeddings = embedding_provider
        self._telemetry = telemetry

    async def process_document(self, document_id: UUID) -> None:
        organization_id = self._scope.organization_id

        claimed = await self._repository.claim_for_processing(
            organization_id=organization_id,
            document_id=document_id,
        )
        if not claimed:
            # Check whether the document exists or is in an invalid/already-processed state.
            document = await self._repository.get_document(
                organization_id=organization_id,
                document_id=document_id,
            )
            if document is None:
                raise NotFoundError()
            raise ConflictError("The document is not ready for extraction.")

        document = await self._repository.get_document(
            organization_id=organization_id,
            document_id=document_id,
        )
        if document is None:
            raise NotFoundError()

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
            body = await self._document_store.get_object_bounded(
                key=document.storage_key, max_bytes=MAX_UPLOAD_BYTES
            )
        except ObjectSizeLimitError as exc:
            error = "The document exceeds the upload size policy."
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
                    payload={"document_id": str(document.id), "reason": error},
                    occurred_at=datetime.now(UTC),
                )
            )
            raise ConflictError(error) from exc
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
                if not text.strip():
                    raise _DocumentExtractionError(
                        "The document contains no extractable text."
                    )

            if len(text) > MAX_EXTRACTED_CHARACTERS:
                raise _DocumentExtractionError("The extracted text exceeds the processing limit.")

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
            if not chunks:
                raise _DocumentExtractionError(
                    "The document contains no extractable text."
                )

            await self._repository.add_chunks(
                organization_id=organization_id,
                document_id=document.id,
                chunks=chunks,
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

        except _DocumentExtractionError as exc:
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

        # 4. Generate embeddings for document chunks
        _embedding_failure_msg = "The document could not be embedded."
        start_time = time.perf_counter()
        try:
            # Detect on the complete normalized document before slicing, so a
            # sensitive value spanning a stored chunk boundary is fully masked.
            minimized = minimize_model_text(
                text.strip(), surface="DOCUMENT_EMBEDDING", preserve_length=True
            )
            texts_to_embed = [
                minimized[
                    chunk.chunk_index * TEXT_CHUNK_SIZE:
                    chunk.chunk_index * TEXT_CHUNK_SIZE + len(chunk.content)
                ]
                for chunk in chunks
            ]
            embedding_result = await self._embeddings.embed(texts_to_embed)
            if len(embedding_result.vectors) != len(chunks):
                raise ValueError("Embedding count does not match chunk count.")
        except Exception as exc:
            await self._repository.set_status(
                organization_id=organization_id,
                document_id=document.id,
                status=ProcessingStatus.FAILED,
                error=_embedding_failure_msg,
            )
            await self._events.publish(
                DomainEvent(
                    event_type="DocumentProcessingFailed",
                    organization_id=str(organization_id),
                    payload={
                        "document_id": str(document.id),
                        "reason": _embedding_failure_msg,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )
            raise UpstreamError(_embedding_failure_msg) from exc
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if self._telemetry is not None:
            model_id = getattr(embedding_result, "model_id", "amazon.titan-embed-text-v2:0")
            safe_telemetry_call(
                self._telemetry.record_latency,
                "document_embedding",
                elapsed_ms,
                dimensions={"Operation": "document_processing", "ModelId": model_id},
            )
            if getattr(embedding_result, "input_tokens", None):
                safe_telemetry_call(
                    self._telemetry.record_token_usage,
                    model_id=model_id,
                    input_tokens=embedding_result.input_tokens,
                    output_tokens=0,
                    operation="document_processing",
                )

        # 5. Persist embeddings via VectorStore
        _vector_failure_msg = "The document vectors could not be saved."
        conf_val = (
            document.confidentiality_level.value
            if hasattr(document.confidentiality_level, "value")
            else str(document.confidentiality_level)
        )
        vector_records = [
            VectorRecord(
                chunk_id=str(chunk.id),
                document_id=str(document.id),
                organization_id=str(organization_id),
                embedding=vector,
                content=chunk.content,
                metadata={
                    "confidentiality_level": conf_val,
                    "content_type": document.content_type,
                    "filename": document.filename,
                    "chunk_index": chunk.chunk_index,
                },
            )
            for chunk, vector in zip(chunks, embedding_result.vectors, strict=True)
        ]

        try:
            updated_count = await self._vectors.upsert(vector_records)
            if updated_count != len(chunks):
                raise ValueError(
                    f"Expected {len(chunks)} vectors upserted, got {updated_count}"
                )
        except Exception as exc:
            await self._repository.set_status(
                organization_id=organization_id,
                document_id=document.id,
                status=ProcessingStatus.FAILED,
                error=_vector_failure_msg,
            )
            await self._events.publish(
                DomainEvent(
                    event_type="DocumentProcessingFailed",
                    organization_id=str(organization_id),
                    payload={
                        "document_id": str(document.id),
                        "reason": _vector_failure_msg,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )
            raise UpstreamError(_vector_failure_msg) from exc

        # 6. Mark document as READY and publish DocumentIndexed event
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

    def _extract_pdf_text(self, body: bytes) -> tuple[str, str | None]:
        """Extract plain text from a PDF byte payload.

        Returns ``(text, None)`` on success, or ``("", reason)`` when the
        document must be quarantined (e.g. encrypted PDF). Raises
        ``_DocumentExtractionError`` for malformed/unreadable files.
        """
        import pypdf
        from pypdf.errors import PdfReadError

        try:
            reader = pypdf.PdfReader(io.BytesIO(body))
        except PdfReadError as exc:
            raise _PdfProcessingError("The PDF could not be read.") from exc
        except Exception as exc:
            raise _PdfProcessingError("The PDF could not be read.") from exc

        if reader.is_encrypted:
            return "", "The PDF is encrypted and cannot be processed."

        if len(reader.pages) > MAX_PDF_PAGES:
            raise _PdfProcessingError("The PDF exceeds the page limit.")

        pages: list[str] = []
        character_count = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            character_count += len(page_text) + (1 if pages else 0)
            if character_count > MAX_EXTRACTED_CHARACTERS:
                raise _PdfProcessingError("The extracted text exceeds the processing limit.")
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
        if len(text) > MAX_EXTRACTED_CHARACTERS:
            raise _DocumentExtractionError("The extracted text exceeds the processing limit.")
        normalized = text.strip()
        if not normalized:
            return []

        if (len(normalized) + TEXT_CHUNK_SIZE - 1) // TEXT_CHUNK_SIZE > MAX_DOCUMENT_CHUNKS:
            raise _DocumentExtractionError("The document exceeds the chunk limit.")

        pieces = [
            normalized[index : index + TEXT_CHUNK_SIZE]
            for index in range(0, len(normalized), TEXT_CHUNK_SIZE)
        ]

        findings = DeterministicSensitiveDataDetector().detect(normalized)

        return [
            ProcessingChunk(
                chunk_index=index,
                content=content,
                token_count=max(1, len(content.split())),
                metadata={
                    "content_type": document.content_type,
                    "filename": document.filename,
                    "model_data_policy_version": POLICY_VERSION,
                    "sensitive_data_categories": sorted({
                        finding.category for finding in findings
                        if finding.start < (index + 1) * TEXT_CHUNK_SIZE
                        and finding.end > index * TEXT_CHUNK_SIZE
                    }),
                },
            )
            for index, content in enumerate(pieces)
        ]


class _DocumentExtractionError(Exception):
    """Raised when document text extraction fails for a non-quarantine reason."""

    pass


class _PdfProcessingError(_DocumentExtractionError):
    """Raised when PDF text extraction fails for a non-quarantine reason."""

    pass
