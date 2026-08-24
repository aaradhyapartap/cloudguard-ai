"""Initial schema: organizations, users, documents — with Row-Level Security.

Revision ID: 0001
Revises:
Create Date: Phase 1

The RLS section at the bottom is the part that matters. Without it,
``organization_id`` is a column that the application promises to filter on.
With it, PostgreSQL refuses to return another tenant's rows even when the
application forgets — which is the difference between a convention and a
control.

Note ``FORCE ROW LEVEL SECURITY``. Plain ``ENABLE`` exempts the table owner, and
in a small deployment the application often *is* the table owner, which silently
disables the protection you just wrote. ``FORCE`` applies the policy to
everyone including the owner. This single keyword is the most common way an RLS
implementation ends up doing nothing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_SETTING = "app.current_organization_id"

ROLE_VALUES = ("analyst", "manager", "admin")
DOCUMENT_TYPE_VALUES = (
    "policy",
    "audit_report",
    "control_documentation",
    "financial_report",
    "invoice",
    "erp_export",
    "sop",
    "risk_report",
    "vendor_document",
    "contract",
    "security_policy",
    "unknown",
)
CONFIDENTIALITY_VALUES = ("public", "internal", "confidential", "restricted")
PROCESSING_STATUS_VALUES = (
    "queued",
    "extracting",
    "indexing",
    "ready",
    "failed",
    "quarantined",
)

TENANT_TABLES = ("users", "documents")


def _create_enum_type(values: tuple[str, ...], name: str) -> postgresql.ENUM:
    """Create the type once, then return a handle that will not re-create it.

    SQLAlchemy's ENUM emits CREATE TYPE implicitly when the column is added by
    create_table. Since the type is created explicitly here (so that ordering
    and checkfirst are under our control), the handle passed to create_table
    must carry create_type=False — otherwise the second CREATE TYPE fails with
    DuplicateObject and the migration dies halfway through.
    """
    postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    role_enum = _create_enum_type(ROLE_VALUES, "role")
    document_type_enum = _create_enum_type(DOCUMENT_TYPE_VALUES, "document_type")
    confidentiality_enum = _create_enum_type(
        CONFIDENTIALITY_VALUES, "confidentiality_level"
    )
    processing_status_enum = _create_enum_type(
        PROCESSING_STATUS_VALUES, "processing_status"
    )

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200)),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("department", sa.String(120)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("content_type", sa.String(160), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "document_type", document_type_enum, nullable=False, server_default="unknown"
        ),
        sa.Column(
            "confidentiality_level",
            confidentiality_enum,
            nullable=False,
            server_default="internal",
        ),
        sa.Column(
            "processing_status",
            processing_status_enum,
            nullable=False,
            server_default="queued",
        ),
        sa.Column("processing_error", sa.Text),
        sa.Column(
            "uploader_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("department", sa.String(120)),
        sa.Column("source", sa.String(200)),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_documents_org_status", "documents", ["organization_id", "processing_status"]
    )
    op.create_index(
        "ix_documents_org_type", "documents", ["organization_id", "document_type"]
    )
    op.create_index(
        "ix_documents_org_created", "documents", ["organization_id", "created_at"]
    )

    # ------------------------------------------------------------------ RLS
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # current_setting(..., true) returns NULL rather than erroring when the
        # setting is absent. With NULL the predicate is false, so a session that
        # never set a tenant sees nothing. Failing closed is the correct default.
        predicate = (
            "organization_id = "
            f"NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
        )
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("documents")
    op.drop_table("users")
    op.drop_table("organizations")

    for enum_name in ("processing_status", "confidentiality_level", "document_type", "role"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
