"""ORM tables.

Phase 1 defines only what the foundation needs: Organization, User, Document.
Phase 3 extends Document and adds DocumentChunk; Phase 5 adds Finding and Risk.
They are added when the phase that uses them arrives — an unused table is a
migration you have to maintain for a feature that may change shape before it
exists.

Two conventions applied to every tenant-owned table:

* ``organization_id`` is NOT NULL and indexed. It is the RLS predicate, so it is
  on the hot path of literally every query.
* Timestamps are ``timezone=True`` and default to ``now()`` in the **database**,
  not in Python. Application clocks disagree; the database clock is the one that
  orders an audit trail.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enums import ConfidentialityLevel, DocumentType, ProcessingStatus, Role
from app.repositories.database import Base


def _pg_enum(python_enum: type, name: str) -> ENUM:
    """Native PostgreSQL enum.

    A CHECK-constrained text column would also work. A real enum type is chosen
    because an invalid value becomes impossible to insert rather than merely
    discouraged, and because the type is self-documenting in ``\\d+`` output.
    """
    return ENUM(
        python_enum,
        name=name,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
        create_type=False,  # migrations own type creation
    )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    settings: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
        Index("ix_users_organization_id", "organization_id"),
    )

    # Equal to the Cognito `sub` claim. Using the IdP's identifier as the primary
    # key removes an entire class of "which id is this?" bug at the auth boundary.
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(_pg_enum(Role, "role"), nullable=False)
    department: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_org_status", "organization_id", "processing_status"),
        Index("ix_documents_org_type", "organization_id", "document_type"),
        Index("ix_documents_org_created", "organization_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")

    document_type: Mapped[DocumentType] = mapped_column(
        _pg_enum(DocumentType, "document_type"),
        nullable=False,
        server_default=DocumentType.UNKNOWN.value,
    )
    confidentiality_level: Mapped[ConfidentialityLevel] = mapped_column(
        _pg_enum(ConfidentialityLevel, "confidentiality_level"),
        nullable=False,
        server_default=ConfidentialityLevel.INTERNAL.value,
    )
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        _pg_enum(ProcessingStatus, "processing_status"),
        nullable=False,
        server_default=ProcessingStatus.QUEUED.value,
    )
    processing_error: Mapped[str | None] = mapped_column(Text)

    uploader_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    department: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str | None] = mapped_column(String(200))
    tags: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="[]")

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
        Index(
            "ix_document_chunks_org_document",
            "organization_id",
            "document_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(
        nullable=False,
        server_default="0",
    )
    chunk_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )
