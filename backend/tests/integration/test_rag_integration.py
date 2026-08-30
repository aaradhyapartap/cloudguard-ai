"""Integration tests for RAGService and RAG API against PostgreSQL + pgvector.

Run with:
    RUN_DB_TESTS=1 pytest tests/integration/test_rag_integration.py -m integration
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from app.adapters.local.identity import LocalIdentityProvider
from app.adapters.local.vector_store import SQLAlchemyVectorStore
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.adapters.mock.llm import MockLLMProvider
from app.models.ai import VectorRecord
from app.models.enums import Role
from app.models.principal import Principal
from app.models.rag import RAGRequest
from app.repositories.database import tenant_session, untenanted_session
from app.services.rag import RAGService
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
async def seed_rag_test_corpus() -> object:
    _skip_without_database()

    # Seed organizations
    async with untenanted_session() as session:
        for org_id, label in ((ORG_A, "rag-acme"), (ORG_B, "rag-globex")):
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
        (ORG_A, USER_A, "a@acme.rag.test"),
        (ORG_B, USER_B, "b@globex.rag.test"),
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
            "acme_incident_response.pdf",
            "internal",
            CHUNK_A1,
            "Acme incident response policy requires 1-hour containment.",
        ),
        (
            ORG_A,
            DOC_A2,
            USER_A,
            "acme_executive_compensation.pdf",
            "restricted",
            CHUNK_A2,
            "Acme C-suite compensation plan and equity allocation.",
        ),
        (
            ORG_B,
            DOC_B1,
            USER_B,
            "globex_incident_response.pdf",
            "internal",
            CHUNK_B1,
            "Globex incident response policy requires 4-hour containment.",
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
                    "key": f"rag-test/{filename}",
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
        email="test@rag.test",
        department="Security",
    )


async def test_rag_service_e2e_against_pgvector() -> None:
    _skip_without_database()
    retrieval_service = RetrievalService(
        embedding_provider=MockEmbeddingProvider(),
        vector_store=SQLAlchemyVectorStore(),
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=MockLLMProvider(),
    )

    principal = _make_principal(Role.ANALYST, organization_id=ORG_A)
    request = RAGRequest(
        query="What is the containment timeframe for incident response?",
        top_k=5,
    )

    response = await rag_service.generate_answer(principal=principal, request=request)

    assert response.retrieval_count >= 1
    assert len(response.sources) >= 1
    assert response.sources[0].label == "S1"
    assert response.sources[0].chunk_id == str(CHUNK_A1)
    assert response.sources[0].document_id == str(DOC_A1)
    assert len(response.answer) > 0


async def test_rag_service_cross_tenant_isolation_pgvector() -> None:
    _skip_without_database()
    retrieval_service = RetrievalService(
        embedding_provider=MockEmbeddingProvider(),
        vector_store=SQLAlchemyVectorStore(),
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=MockLLMProvider(),
    )

    # Query matching Globex policy, but executed by Org A principal
    principal_a = _make_principal(Role.ANALYST, organization_id=ORG_A)
    request = RAGRequest(
        query="Globex incident response policy requires 4-hour containment.",
        top_k=10,
    )
    response_a = await rag_service.generate_answer(principal=principal_a, request=request)

    # Must contain zero Globex sources
    source_chunk_ids = {s.chunk_id for s in response_a.sources}
    assert str(CHUNK_B1) not in source_chunk_ids


async def test_rag_service_confidentiality_enforcement_pgvector() -> None:
    _skip_without_database()
    retrieval_service = RetrievalService(
        embedding_provider=MockEmbeddingProvider(),
        vector_store=SQLAlchemyVectorStore(),
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=MockLLMProvider(),
    )

    request = RAGRequest(
        query="Acme C-suite compensation plan and equity allocation.",
        top_k=5,
    )

    # 1. Analyst from Org A (clearance INTERNAL) cannot retrieve RESTRICTED chunk
    analyst_a = _make_principal(Role.ANALYST, organization_id=ORG_A)
    resp_analyst = await rag_service.generate_answer(principal=analyst_a, request=request)
    assert not any(s.chunk_id == str(CHUNK_A2) for s in resp_analyst.sources)

    # 2. Admin from Org A (clearance RESTRICTED) CAN retrieve RESTRICTED chunk
    admin_a = _make_principal(Role.ADMIN, organization_id=ORG_A)
    resp_admin = await rag_service.generate_answer(principal=admin_a, request=request)
    assert any(s.chunk_id == str(CHUNK_A2) for s in resp_admin.sources)


def test_rag_api_endpoint_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/rag/query",
        json={"query": "What is the policy?", "top_k": 5},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_rag_api_endpoint_response_schema_and_no_raw_vectors(
    client: TestClient,
    token_signer: LocalIdentityProvider,
) -> None:
    _skip_without_database()
    principal = _make_principal(Role.ANALYST, organization_id=ORG_A)
    headers = bearer(token_signer, principal)

    response = client.post(
        "/api/v1/rag/query",
        headers=headers,
        json={"query": "incident response containment", "top_k": 5},
    )
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert "sources" in data
    assert "retrieval_count" in data
    assert "model_id" in data
    assert isinstance(data["sources"], list)

    for source in data["sources"]:
        assert "label" in source
        assert "chunk_id" in source
        assert "document_id" in source
        assert "score" in source
        assert "metadata" in source
        # Assert NO raw embedding vector is exposed in API response
        assert "embedding" not in source
        assert "vector" not in source
