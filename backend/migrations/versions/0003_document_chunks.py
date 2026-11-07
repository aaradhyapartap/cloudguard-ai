"""Add document chunks for Phase 3 ingestion.

Revision ID: 0003
Revises: 0002
Create Date: Phase 3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TENANT_SETTING = "app.current_organization_id"


def upgrade() -> None:
    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "metadata",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
    )

    op.create_index(
        "ix_document_chunks_org_document",
        "document_chunks",
        ["organization_id", "document_id"],
    )

    op.execute("ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_chunks FORCE ROW LEVEL SECURITY")

    predicate = f"organization_id = NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
    op.execute(
        "CREATE POLICY document_chunks_tenant_isolation ON document_chunks "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS document_chunks_tenant_isolation ON document_chunks")
    op.execute("ALTER TABLE document_chunks DISABLE ROW LEVEL SECURITY")
    op.drop_table("document_chunks")
