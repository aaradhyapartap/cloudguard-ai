"""Integration tests for pgvector persistence and tenant isolation against PostgreSQL.

Run with:
    RUN_DB_TESTS=1 pytest tests/integration/test_vector_integration.py -m integration
"""

from __future__ import annotations

import math
import os
from uuid import uuid4

import pytest
from app.adapters.local.vector_store import SQLAlchemyVectorStore
from app.models.ai import VectorRecord
from app.models.enums import ConfidentialityLevel
from app.repositories.database import (
    tenant_session,
    untenanted_session,
)
from sqlalchemy import text

pytestmark = pytest.mark.integration

ORG_A = uuid4()
ORG_B = uuid4()
USER_A = uuid4()
USER_B = uuid4()

DOC_A1 = uuid4()  # internal
DOC_A2 = uuid4()  # restricted
DOC_B1 = uuid4()  # internal

CHUNK_A1 = uuid4()
CHUNK_A2 = uuid4()
CHUNK_B1 = uuid4()


def _skip_without_database() -> None:
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 with PostgreSQL running")


def _make_vector(seed: int) -> list[float]:
    """Generate deterministic 1024-d unit vector."""
    raw = [math.sin(seed * (i + 1) * 0.001) for i in range(1024)]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


@pytest.fixture(scope="module", autouse=True)
async def seed_vector_test_corpus() -> object:
    _skip_without_database()

    # Seed organizations
    async with untenanted_session() as session:
        for org_id, label in ((ORG_A, "vector-acme"), (ORG_B, "vector-globex")):
            await session.execute(
                text(
                    "INSERT INTO organizations (id, name, slug) "
                    "VALUES (:id, :name, :slug) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": org_id,
                    "name": label.title(),
                    "slug": f"{label}-{org_id.hex[:12]}",
                },
            )

    # Seed users and documents
    for org_id, user_id, email in (
        (ORG_A, USER_A, "a@acme.vector.test"),
        (ORG_B, USER_B, "b@globex.vector.test"),
    ):
        async with tenant_session(org_id) as session:
            await session.execute(
                text(
                    "INSERT INTO users (id, organization_id, email, role) "
                    "VALUES (:id, :org, :email, CAST('analyst' AS role)) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": user_id, "org": org_id, "email": email},
            )

    # Seed documents and placeholder chunks
    doc_seeds = [
        (ORG_A, DOC_A1, USER_A, "acme_policy.pdf", "internal", CHUNK_A1, "Acme policy chunk"),
        (ORG_A, DOC_A2, USER_A, "acme_secret.pdf", "restricted", CHUNK_A2, "Acme secret chunk"),
        (ORG_B, DOC_B1, USER_B, "globex_policy.pdf", "internal", CHUNK_B1, "Globex policy chunk"),
    ]

    for org_id, doc_id, uploader_id, filename, conf_level, chunk_id, chunk_content in doc_seeds:
        async with tenant_session(org_id) as session:
            await session.execute(
                text(
                    "INSERT INTO documents "
                    "(id, organization_id, filename, storage_key, content_type, "
                    "confidentiality_level, processing_status, uploader_id) "
                    "VALUES (:id, :org, :filename, :key, 'application/pdf', "
                    "CAST(:conf AS confidentiality_level), "
                    "CAST('ready' AS processing_status), :uploader) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": doc_id,
                    "org": org_id,
                    "filename": filename,
                    "key": f"test/{filename}",
                    "conf": conf_level,
                    "uploader": uploader_id,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO document_chunks "
                    "(id, organization_id, document_id, chunk_index, "
                    "content, token_count, metadata) "
                    "VALUES (:id, :org, :doc, 0, :content, 10, CAST(:meta AS jsonb)) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": chunk_id,
                    "org": org_id,
                    "doc": doc_id,
                    "content": chunk_content,
                    "meta": '{"page": 1}',
                },
            )


async def test_pgvector_extension_is_active() -> None:
    _skip_without_database()
    async with untenanted_session() as session:
        result = await session.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        version = result.scalar_one_or_none()
        assert version is not None
        assert len(version) > 0


async def test_sqlalchemy_vector_store_upsert_and_similarity_search() -> None:
    _skip_without_database()
    store = SQLAlchemyVectorStore()

    vec_a1 = _make_vector(100)
    vec_a2 = _make_vector(200)
    vec_b1 = _make_vector(100)  # Identical to vec_a1 to test tenant isolation

    # Upsert embeddings into chunks
    records = [
        VectorRecord(
            chunk_id=str(CHUNK_A1),
            document_id=str(DOC_A1),
            organization_id=str(ORG_A),
            embedding=vec_a1,
            content="Acme policy chunk",
        ),
        VectorRecord(
            chunk_id=str(CHUNK_A2),
            document_id=str(DOC_A2),
            organization_id=str(ORG_A),
            embedding=vec_a2,
            content="Acme secret chunk",
        ),
        VectorRecord(
            chunk_id=str(CHUNK_B1),
            document_id=str(DOC_B1),
            organization_id=str(ORG_B),
            embedding=vec_b1,
            content="Globex policy chunk",
        ),
    ]

    updated = await store.upsert(records)
    assert updated == 3

    # Query with vec_a1 as query embedding for Tenant A
    matches = await store.search(
        embedding=vec_a1,
        organization_id=ORG_A,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL, ConfidentialityLevel.CONFIDENTIAL),
        top_k=5,
    )

    # Must find CHUNK_A1 with similarity ~ 1.0
    assert len(matches) == 1
    assert matches[0].chunk_id == str(CHUNK_A1)
    assert matches[0].document_id == str(DOC_A1)
    assert matches[0].score > 0.999
    assert matches[0].content == "Acme policy chunk"


async def test_upsert_with_wrong_document_id_does_not_update_chunk() -> None:
    _skip_without_database()
    store = SQLAlchemyVectorStore()

    # Attempt to update CHUNK_A1 with wrong document_id (DOC_A2)
    wrong_vec = _make_vector(999)
    record = VectorRecord(
        chunk_id=str(CHUNK_A1),
        document_id=str(DOC_A2),  # Wrong document_id
        organization_id=str(ORG_A),
        embedding=wrong_vec,
        content="Forged chunk",
    )

    updated = await store.upsert([record])
    assert updated == 0

    # Verify CHUNK_A1 still has original embedding (matches vec_a1 ~ 1.0)
    orig_vec = _make_vector(100)
    matches = await store.search(
        embedding=orig_vec,
        organization_id=ORG_A,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
        top_k=5,
    )
    assert len(matches) == 1
    assert matches[0].chunk_id == str(CHUNK_A1)
    assert matches[0].score > 0.999


async def test_search_enforces_tenant_isolation_strictly() -> None:
    _skip_without_database()
    store = SQLAlchemyVectorStore()
    query_vec = _make_vector(100)

    # Searching in ORG_A must NEVER return ORG_B chunks
    matches_a = await store.search(
        embedding=query_vec,
        organization_id=ORG_A,
        confidentiality_levels=(
            ConfidentialityLevel.PUBLIC,
            ConfidentialityLevel.INTERNAL,
            ConfidentialityLevel.CONFIDENTIAL,
            ConfidentialityLevel.RESTRICTED,
        ),
        top_k=10,
    )
    chunk_ids_a = {m.chunk_id for m in matches_a}
    assert str(CHUNK_B1) not in chunk_ids_a

    # Searching in ORG_B must only return ORG_B chunks
    matches_b = await store.search(
        embedding=query_vec,
        organization_id=ORG_B,
        confidentiality_levels=(
            ConfidentialityLevel.PUBLIC,
            ConfidentialityLevel.INTERNAL,
        ),
        top_k=10,
    )
    assert len(matches_b) == 1
    assert matches_b[0].chunk_id == str(CHUNK_B1)


async def test_search_enforces_confidentiality_clearance_filtering() -> None:
    _skip_without_database()
    store = SQLAlchemyVectorStore()

    # Query with vec_a2 (closest to CHUNK_A2, which is RESTRICTED)
    query_vec = _make_vector(200)

    # Caller with only INTERNAL clearance must not see RESTRICTED document chunk
    matches_internal = await store.search(
        embedding=query_vec,
        organization_id=ORG_A,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
        top_k=5,
    )
    assert not any(m.chunk_id == str(CHUNK_A2) for m in matches_internal)

    # Caller with RESTRICTED clearance sees CHUNK_A2
    matches_restricted = await store.search(
        embedding=query_vec,
        organization_id=ORG_A,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL, ConfidentialityLevel.RESTRICTED),
        top_k=5,
    )
    assert any(m.chunk_id == str(CHUNK_A2) for m in matches_restricted)


async def test_search_scoped_by_document_ids() -> None:
    _skip_without_database()
    store = SQLAlchemyVectorStore()
    query_vec = _make_vector(100)

    matches = await store.search(
        embedding=query_vec,
        organization_id=ORG_A,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL, ConfidentialityLevel.RESTRICTED),
        top_k=5,
        document_ids=[DOC_A1],
    )
    assert len(matches) == 1
    assert matches[0].document_id == str(DOC_A1)


async def test_delete_by_document_clears_vectors_while_retaining_chunks() -> None:
    _skip_without_database()
    store = SQLAlchemyVectorStore()

    # Clear vectors for DOC_A2
    cleared = await store.delete_by_document(
        document_id=DOC_A2,
        organization_id=ORG_A,
    )
    assert cleared >= 1

    # 1. Verify vector is gone from search
    query_vec = _make_vector(200)
    matches = await store.search(
        embedding=query_vec,
        organization_id=ORG_A,
        confidentiality_levels=(ConfidentialityLevel.RESTRICTED,),
        top_k=5,
    )
    assert not any(m.chunk_id == str(CHUNK_A2) for m in matches)

    # 2. Verify DocumentChunk row is NOT deleted and content/metadata are intact
    async with tenant_session(ORG_A) as session:
        result = await session.execute(
            text(
                "SELECT id, content, metadata, embedding "
                "FROM document_chunks "
                "WHERE id = :id AND organization_id = :org"
            ),
            {"id": CHUNK_A2, "org": ORG_A},
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == CHUNK_A2
        assert row[1] == "Acme secret chunk"
        assert row[2] == {"page": 1}
        assert row[3] is None  # embedding was cleared to NULL

    # 3. Verify other tenant (ORG_B) chunk and vector remain unaffected
    async with tenant_session(ORG_B) as session:
        result = await session.execute(
            text(
                "SELECT id, embedding FROM document_chunks "
                "WHERE id = :id AND organization_id = :org"
            ),
            {"id": CHUNK_B1, "org": ORG_B},
        )
        row_b = result.fetchone()
        assert row_b is not None
        assert row_b[1] is not None
