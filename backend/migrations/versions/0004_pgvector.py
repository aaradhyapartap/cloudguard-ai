"""Add pgvector extension and embedding column to document_chunks.

Revision ID: 0004
Revises: 0003
Create Date: Phase 4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Add nullable embedding column (1024 dimensions for Titan Embeddings v2)
    op.add_column(
        "document_chunks",
        sa.Column("embedding", Vector(1024), nullable=True),
    )

    # Cosine distance index for similarity queries
    op.create_index(
        "ix_document_chunks_embedding",
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_embedding", table_name="document_chunks")
    op.drop_column("document_chunks", "embedding")
