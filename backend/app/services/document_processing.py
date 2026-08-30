"""Internal document extraction, chunking, embedding, and vector indexing service."""

from __future__ import annotations

import io
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
from app.ports.vector_store import VectorStore

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
    ) -> None:
        self._scope = scope
        self._repository = repository
        self._document_store = document_store
        self._events = event_publisher
        self._vectors = vector_store
        self._embeddings = embedding_provider

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
                if not text.strip():
                    raise _DocumentExtractionError(
                        "The document contains no extractable text."
                    )

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
        try:
            texts_to_embed = [chunk.content for chunk in chunks]
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


class _DocumentExtractionError(Exception):
    """Raised when document text extraction fails for a non-quarantine reason."""

    pass


class _PdfProcessingError(_DocumentExtractionError):
    """Raised when PDF text extraction fails for a non-quarantine reason."""

    pass
