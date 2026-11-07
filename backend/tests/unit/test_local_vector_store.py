"""Unit tests for SQLAlchemyVectorStore query compilation and validation."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from app.adapters.local.vector_store import SQLAlchemyVectorStore
from app.models.ai import VectorRecord
from app.models.enums import ConfidentialityLevel
from app.repositories.tables import Document, DocumentChunk
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql


def test_local_vector_store_upsert_compiles_correct_statement() -> None:
    org_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()
    dummy_vec = [0.0] * 1024

    stmt = (
        update(DocumentChunk)
        .where(
            DocumentChunk.organization_id == org_id,
            DocumentChunk.document_id == doc_id,
            DocumentChunk.id == chunk_id,
        )
        .values(embedding=dummy_vec)
    )

    compiled = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    )
    assert "UPDATE document_chunks" in compiled
    assert "SET embedding=" in compiled
    assert "WHERE document_chunks.organization_id =" in compiled
    assert "document_chunks.document_id =" in compiled
    assert "document_chunks.id =" in compiled


def test_local_vector_store_search_compiles_cosine_similarity_query() -> None:
    org_id = uuid4()
    doc_id = uuid4()
    dummy_vec = [0.1] * 1024
    confidentiality_levels = (ConfidentialityLevel.INTERNAL, ConfidentialityLevel.CONFIDENTIAL)

    distance_expr = DocumentChunk.embedding.cosine_distance(dummy_vec)
    score_expr = (1.0 - distance_expr).label("score")

    stmt = (
        select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            DocumentChunk.content,
            DocumentChunk.chunk_metadata,
            score_expr,
        )
        .join(
            Document,
            (Document.id == DocumentChunk.document_id)
            & (Document.organization_id == DocumentChunk.organization_id),
        )
        .where(
            DocumentChunk.organization_id == org_id,
            DocumentChunk.embedding.is_not(None),
            Document.confidentiality_level.in_(confidentiality_levels),
            DocumentChunk.document_id.in_([doc_id]),
        )
        .order_by(distance_expr.asc())
        .limit(10)
    )

    compiled = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    )
    assert "SELECT document_chunks.id, document_chunks.document_id" in compiled
    assert "<=>" in compiled  # pgvector cosine distance operator
    assert "JOIN documents ON documents.id = document_chunks.document_id" in compiled
    assert "document_chunks.organization_id =" in compiled
    assert "document_chunks.embedding IS NOT NULL" in compiled
    assert "documents.confidentiality_level IN" in compiled
    assert "document_chunks.document_id IN" in compiled
    assert "ORDER BY (document_chunks.embedding <=>" in compiled
    assert "LIMIT" in compiled


def test_local_vector_store_delete_by_document_compiles_null_update() -> None:
    org_id = uuid4()
    doc_id = uuid4()

    stmt = (
        update(DocumentChunk)
        .where(
            DocumentChunk.organization_id == org_id,
            DocumentChunk.document_id == doc_id,
            DocumentChunk.embedding.is_not(None),
        )
        .values(embedding=None)
    )

    compiled = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    )
    assert "UPDATE document_chunks" in compiled
    assert "SET embedding=" in compiled
    assert "WHERE document_chunks.organization_id =" in compiled
    assert "document_chunks.document_id =" in compiled
    assert "document_chunks.embedding IS NOT NULL" in compiled


@pytest.mark.parametrize("invalid_vec", [
    [0.1] * 1023,
    [0.1] * 1025,
    [float("nan")] * 1024,
    [float("inf")] * 1024,
    [float("-inf")] * 1024,
])
async def test_local_vector_store_upsert_validation(invalid_vec: list[Any]) -> None:
    store = SQLAlchemyVectorStore()

    with pytest.raises(ValueError, match="Invalid chunk"):
        await store.upsert(
            [
                VectorRecord(
                    chunk_id=str(uuid4()),
                    document_id=str(uuid4()),
                    organization_id=str(uuid4()),
                    embedding=invalid_vec,
                    content="chunk",
                )
            ]
        )


async def test_local_vector_store_empty_upsert() -> None:
    store = SQLAlchemyVectorStore()
    assert await store.upsert([]) == 0


@pytest.mark.parametrize("invalid_top_k", [0, -1, 101])
async def test_local_vector_store_search_top_k_validation(invalid_top_k: int) -> None:
    store = SQLAlchemyVectorStore()
    with pytest.raises(ValueError, match="top_k must be an integer between"):
        await store.search(
            embedding=[0.1] * 1024,
            organization_id=uuid4(),
            confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
            top_k=invalid_top_k,
        )


async def test_local_vector_store_search_empty_clearance() -> None:
    store = SQLAlchemyVectorStore()
    results = await store.search(
        embedding=[0.1] * 1024,
        organization_id=uuid4(),
        confidentiality_levels=(),
    )
    assert results == []
