"""Document request and response models for the ingestion API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.models.common import DomainModel
from app.models.enums import ConfidentialityLevel, DocumentType, ProcessingStatus


class DocumentCreateRequest(DomainModel):
    """Metadata supplied before a document is uploaded."""

    filename: str = Field(min_length=1, max_length=512)
    content_type: str = Field(min_length=1, max_length=160)
    size_bytes: int = Field(ge=0)
    document_type: DocumentType = DocumentType.UNKNOWN
    confidentiality_level: ConfidentialityLevel = ConfidentialityLevel.INTERNAL
    department: str | None = Field(default=None, max_length=120)
    source: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=50)


class DocumentResponse(DomainModel):
    """Server-authoritative representation of a document."""

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    document_type: DocumentType
    confidentiality_level: ConfidentialityLevel
    processing_status: ProcessingStatus
    processing_error: str | None
    uploader_id: UUID
    department: str | None
    source: str | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(DomainModel):
    """Document registration plus its short-lived upload destination."""

    document: DocumentResponse
    upload_url: str
    expires_in_seconds: int
