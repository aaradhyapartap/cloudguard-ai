"""Integration tests for retrieval service and search API against PostgreSQL + pgvector.

Run with:
    RUN_DB_TESTS=1 pytest tests/integration/test_retrieval_integration.py -m integration
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from app.adapters.local.identity import LocalIdentityProvider
from app.adapters.local.vector_store import SQLAlchemyVectorStore
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.models.ai import VectorRecord
from app.models.enums import Role
from app.models.principal import Principal
from app.models.retrieval import RetrievalRequest
from app.repositories.database import tenant_session, untenanted_session
from app.services.retrieval import RetrievalService
from fastapi.testclient import TestClient
from sqlalchemy import text

from conftest import bearer  # type: ignore[import-not-found]

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


@pytest.fixture(scope="module", autouse=True)
async def seed_retrieval_test_corpus() -> object:
    _skip_without_database()

    # Seed organizations
    async with untenanted_session() as session:
        for org_id, label in ((ORG_A, "retrieval-acme"), (ORG_B, "retrieval-globex")):
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

    # Seed users
    for org_id, user_id, email in (
        (ORG_A, USER_A, "a@acme.retrieval.test"),
        (ORG_B, USER_B, "b@globex.retrieval.test"),
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

    # Seed documents and chunks with embeddings
    embeddings = MockEmbeddingProvider()
    doc_seeds = [
        (
            ORG_A,
            DOC_A1,
            USER_A,
            "acme_internal_policy.pdf",
            "internal",
            CHUNK_A1,
            "Acme vendor risk assessment policy chunk.",
        ),
        (
            ORG_A,
            DOC_A2,
            USER_A,
            "acme_restricted_keys.pdf",
            "restricted",
            CHUNK_A2,
            "Acme root cryptographic key management.",
        ),
        (
            ORG_B,
            DOC_B1,
            USER_B,
            "globex_internal_policy.pdf",
            "internal",
            CHUNK_B1,
            "Globex vendor risk assessment policy chunk.",
        ),
    ]

    vector_store = SQLAlchemyVectorStore()
    records_to_upsert: list[VectorRecord] = []

    for org_id, doc_id, uploader_id, filename, conf_level, chunk_id, content in doc_seeds:
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
                    "key": f"retrieval-test/{filename}",
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
                    "content": content,
                    "meta": f'{{"confidentiality_level": "{conf_level}", "page": 1}}',
                },
            )

        res = await embeddings.embed([content])
        records_to_upsert.append(
            VectorRecord(
                chunk_id=str(chunk_id),
                document_id=str(doc_id),
                organization_id=str(org_id),
                embedding=res.vectors[0],
                content=content,
                metadata={"confidentiality_level": conf_level, "page": 1},
            )
        )

    await vector_store.upsert(records_to_upsert)


def _make_principal(
    role: Role = Role.ANALYST,
    organization_id: UUID = ORG_A,
    user_id: UUID = USER_A,
) -> Principal:
    return Principal(
        user_id=user_id,
        organization_id=organization_id,
        role=role,
        email="test@retrieval.test",
        department="Audit",
    )


async def test_retrieval_service_e2e_against_pgvector() -> None:
    _skip_without_database()
    service = RetrievalService(
        embedding_provider=MockEmbeddingProvider(),
        vector_store=SQLAlchemyVectorStore(),
    )

    principal = _make_principal(Role.ANALYST, organization_id=ORG_A)
    request = RetrievalRequest(query="Acme vendor risk assessment policy chunk.", top_k=5)
    response = await service.search(principal=principal, request=request)

    assert response.total >= 1
    assert any(m.chunk_id == str(CHUNK_A1) for m in response.matches)
    match_a1 = next(m for m in response.matches if m.chunk_id == str(CHUNK_A1))
    assert match_a1.score > 0.99
    assert match_a1.content == "Acme vendor risk assessment policy chunk."


async def test_retrieval_service_cross_tenant_isolation_pgvector() -> None:
    _skip_without_database()
    service = RetrievalService(
        embedding_provider=MockEmbeddingProvider(),
        vector_store=SQLAlchemyVectorStore(),
    )

    # Search query that textually matches Globex (Org B) chunk, but execute as Org A principal
    principal_a = _make_principal(Role.ANALYST, organization_id=ORG_A)
    request = RetrievalRequest(query="Globex vendor risk assessment policy chunk.", top_k=10)
    response_a = await service.search(principal=principal_a, request=request)

    # Must contain ZERO Org B chunks
    retrieved_chunk_ids = {m.chunk_id for m in response_a.matches}
    assert str(CHUNK_B1) not in retrieved_chunk_ids


async def test_retrieval_service_confidentiality_clearance_pgvector() -> None:
    _skip_without_database()
    service = RetrievalService(
        embedding_provider=MockEmbeddingProvider(),
        vector_store=SQLAlchemyVectorStore(),
    )

    request = RetrievalRequest(query="Acme root cryptographic key management.", top_k=10)

    # 1. Analyst from Org A (clearance INTERNAL) must NOT see RESTRICTED chunk A2
    analyst_a = _make_principal(Role.ANALYST, organization_id=ORG_A)
    resp_analyst = await service.search(principal=analyst_a, request=request)
    assert not any(m.chunk_id == str(CHUNK_A2) for m in resp_analyst.matches)

    # 2. Admin from Org A (clearance RESTRICTED) MUST see RESTRICTED chunk A2
    admin_a = _make_principal(Role.ADMIN, organization_id=ORG_A)
    resp_admin = await service.search(principal=admin_a, request=request)
    assert any(m.chunk_id == str(CHUNK_A2) for m in resp_admin.matches)


async def test_retrieval_service_cross_tenant_document_id_filter_returns_no_leakage() -> None:
    _skip_without_database()
    service = RetrievalService(
        embedding_provider=MockEmbeddingProvider(),
        vector_store=SQLAlchemyVectorStore(),
    )

    # Org A principal supplies Org B document ID (DOC_B1)
    principal_a = _make_principal(Role.ANALYST, organization_id=ORG_A)
    request = RetrievalRequest(
        query="vendor risk",
        top_k=5,
        document_ids=[DOC_B1],
    )
    response = await service.search(principal=principal_a, request=request)

    # Must return 0 matches - no cross-tenant leakage
    assert response.total == 0
    assert response.matches == []


def test_retrieval_api_endpoint_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/retrieval/search",
        json={"query": "test query", "top_k": 5},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_retrieval_api_endpoint_invalid_payload_returns_422(
    client: TestClient,
    token_signer: LocalIdentityProvider,
) -> None:
    principal = _make_principal(Role.ANALYST, organization_id=ORG_A)
    headers = bearer(token_signer, principal)

    # Empty query
    response = client.post(
        "/api/v1/retrieval/search",
        headers=headers,
        json={"query": "   ", "top_k": 5},
    )
    assert response.status_code == 422

    # Negative top_k
    response = client.post(
        "/api/v1/retrieval/search",
        headers=headers,
        json={"query": "valid query", "top_k": -1},
    )
    assert response.status_code == 422


def test_retrieval_api_endpoint_response_schema_and_no_raw_vectors(
    client: TestClient,
    token_signer: LocalIdentityProvider,
) -> None:
    _skip_without_database()
    principal = _make_principal(Role.ANALYST, organization_id=ORG_A)
    headers = bearer(token_signer, principal)

    response = client.post(
        "/api/v1/retrieval/search",
        headers=headers,
        json={"query": "Acme vendor risk assessment", "top_k": 5},
    )
    assert response.status_code == 200
    data = response.json()
    assert "matches" in data
    assert "total" in data
    assert isinstance(data["matches"], list)

    for match in data["matches"]:
        assert "chunk_id" in match
        assert "document_id" in match
        assert "content" in match
        assert "score" in match
        assert "metadata" in match
        # Assert NO raw embedding vector is exposed in API response
        assert "embedding" not in match
        assert "vector" not in match
