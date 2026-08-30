"""Unit tests for RetrievalService and query search orchestration."""

from uuid import UUID

import pytest
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.core.errors import UpstreamError
from app.models.ai import EmbeddingResult, VectorMatch, VectorRecord
from app.models.enums import ConfidentialityLevel, Role
from app.models.principal import Principal
from app.models.retrieval import RetrievalRequest
from app.ports.llm_provider import EmbeddingProvider
from app.ports.vector_store import VectorStore
from app.services.retrieval import RetrievalService

ORG_A = UUID("11111111-1111-4111-8111-111111111111")
ORG_B = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
DOC_ID_1 = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DOC_ID_2 = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _make_principal(
    role: Role = Role.ANALYST,
    organization_id: UUID = ORG_A,
) -> Principal:
    return Principal(
        user_id=USER_ID,
        organization_id=organization_id,
        role=role,
        email="analyst@cloudguard.ai",
        department="Security",
    )


class _MismatchedCountEmbeddingProvider(EmbeddingProvider):
    @property
    def embedding_model_id(self) -> str:
        return "amazon.titan-embed-text-v2:0"

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        # Deliberately return 2 vectors for 1 text
        return EmbeddingResult(
            vectors=[[0.1] * 1024, [0.2] * 1024],
            model_id=self.embedding_model_id,
            dimensions=1024,
            input_tokens=10,
        )


class _FailingEmbeddingProvider(EmbeddingProvider):
    @property
    def embedding_model_id(self) -> str:
        return "amazon.titan-embed-text-v2:0"

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        raise UpstreamError("Bedrock throttling exception.")


class _FailingVectorStore(VectorStore):
    async def upsert(self, records: list[VectorRecord]) -> int:
        return len(records)

    async def search(
        self,
        *,
        embedding: list[float],
        organization_id: UUID,
        confidentiality_levels: tuple[ConfidentialityLevel, ...],
        top_k: int = 10,
        document_ids: list[UUID] | None = None,
    ) -> list[VectorMatch]:
        raise UpstreamError("Database connection timed out.")

    async def delete_by_document(self, *, document_id: UUID, organization_id: UUID) -> int:
        return 0


async def test_search_success_with_single_query_vector() -> None:
    principal = _make_principal(Role.ANALYST)
    embeddings = MockEmbeddingProvider()
    vector_store = InMemoryVectorStore()

    # Seed one matching chunk
    await vector_store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-1",
                document_id=str(DOC_ID_1),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="CloudGuard AI tenant isolation policy",
                metadata={"confidentiality_level": "internal", "page": 1},
            )
        ]
    )

    service = RetrievalService(
        embedding_provider=embeddings,
        vector_store=vector_store,
    )

    request = RetrievalRequest(query="tenant isolation policy", top_k=5)
    response = await service.search(principal=principal, request=request)

    assert response.total == 1
    assert len(response.matches) == 1
    assert response.matches[0].chunk_id == "chunk-1"
    assert response.matches[0].document_id == str(DOC_ID_1)
    assert response.matches[0].content == "CloudGuard AI tenant isolation policy"
    assert response.matches[0].metadata == {"confidentiality_level": "internal", "page": 1}


async def test_search_preserves_score_descending_order() -> None:
    principal = _make_principal(Role.ANALYST)
    embeddings = MockEmbeddingProvider()
    vector_store = InMemoryVectorStore()

    await vector_store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-low",
                document_id=str(DOC_ID_1),
                organization_id=str(ORG_A),
                embedding=[-1.0] + [0.0] * 1023,
                content="Low similarity chunk",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="chunk-high",
                document_id=str(DOC_ID_1),
                organization_id=str(ORG_A),
                embedding=[1.0] + [0.0] * 1023,
                content="High similarity chunk",
                metadata={"confidentiality_level": "internal"},
            ),
        ]
    )

    service = RetrievalService(
        embedding_provider=embeddings,
        vector_store=vector_store,
    )

    request = RetrievalRequest(query="search", top_k=10)
    response = await service.search(principal=principal, request=request)

    assert response.total == 2
    # Verify scores are in descending order
    assert response.matches[0].score >= response.matches[1].score


async def test_search_with_zero_matches_returns_empty_list() -> None:
    principal = _make_principal(Role.ANALYST)
    embeddings = MockEmbeddingProvider()
    vector_store = InMemoryVectorStore()

    service = RetrievalService(
        embedding_provider=embeddings,
        vector_store=vector_store,
    )

    request = RetrievalRequest(query="unmatched query text", top_k=5)
    response = await service.search(principal=principal, request=request)

    assert response.total == 0
    assert response.matches == []


async def test_search_with_document_ids_filter() -> None:
    principal = _make_principal(Role.ANALYST)
    embeddings = MockEmbeddingProvider()
    vector_store = InMemoryVectorStore()

    await vector_store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-doc1",
                document_id=str(DOC_ID_1),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Doc 1 chunk",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="chunk-doc2",
                document_id=str(DOC_ID_2),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Doc 2 chunk",
                metadata={"confidentiality_level": "internal"},
            ),
        ]
    )

    service = RetrievalService(
        embedding_provider=embeddings,
        vector_store=vector_store,
    )

    # Filter strictly to DOC_ID_1
    request = RetrievalRequest(
        query="test query",
        top_k=5,
        document_ids=[DOC_ID_1],
    )
    response = await service.search(principal=principal, request=request)

    assert response.total == 1
    assert response.matches[0].document_id == str(DOC_ID_1)


async def test_search_rejects_empty_or_whitespace_query() -> None:
    with pytest.raises(ValueError, match=r"Query cannot be empty or whitespace only\."):
        RetrievalRequest(query="   ")


async def test_search_rejects_invalid_top_k() -> None:
    with pytest.raises(ValueError):
        RetrievalRequest(query="valid query", top_k=0)

    with pytest.raises(ValueError):
        RetrievalRequest(query="valid query", top_k=101)


async def test_retrieval_request_deduplicates_document_ids() -> None:
    req = RetrievalRequest(
        query="query",
        document_ids=[DOC_ID_1, DOC_ID_2, DOC_ID_1],
    )
    assert req.document_ids == [DOC_ID_1, DOC_ID_2]


async def test_search_fails_on_embedding_provider_error() -> None:
    principal = _make_principal(Role.ANALYST)
    failing_embeddings = _FailingEmbeddingProvider()
    vector_store = InMemoryVectorStore()

    service = RetrievalService(
        embedding_provider=failing_embeddings,
        vector_store=vector_store,
    )

    request = RetrievalRequest(query="query text", top_k=5)
    with pytest.raises(UpstreamError, match=r"Bedrock throttling exception\."):
        await service.search(principal=principal, request=request)


async def test_search_fails_on_embedding_result_count_mismatch() -> None:
    principal = _make_principal(Role.ANALYST)
    mismatched_embeddings = _MismatchedCountEmbeddingProvider()
    vector_store = InMemoryVectorStore()

    service = RetrievalService(
        embedding_provider=mismatched_embeddings,
        vector_store=vector_store,
    )

    request = RetrievalRequest(query="query text", top_k=5)
    with pytest.raises(UpstreamError, match=r"The query could not be embedded\."):
        await service.search(principal=principal, request=request)


async def test_search_fails_on_vector_store_error() -> None:
    principal = _make_principal(Role.ANALYST)
    embeddings = MockEmbeddingProvider()
    failing_vector_store = _FailingVectorStore()

    service = RetrievalService(
        embedding_provider=embeddings,
        vector_store=failing_vector_store,
    )

    request = RetrievalRequest(query="query text", top_k=5)
    with pytest.raises(UpstreamError, match=r"Database connection timed out\."):
        await service.search(principal=principal, request=request)


async def test_search_clearance_derived_from_principal_role() -> None:
    embeddings = MockEmbeddingProvider()
    vector_store = InMemoryVectorStore()

    # Seed chunks with different confidentiality levels
    await vector_store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-pub",
                document_id=str(DOC_ID_1),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Public chunk",
                metadata={"confidentiality_level": "public"},
            ),
            VectorRecord(
                chunk_id="chunk-int",
                document_id=str(DOC_ID_1),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Internal chunk",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="chunk-conf",
                document_id=str(DOC_ID_1),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Confidential chunk",
                metadata={"confidentiality_level": "confidential"},
            ),
            VectorRecord(
                chunk_id="chunk-rest",
                document_id=str(DOC_ID_1),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Restricted chunk",
                metadata={"confidentiality_level": "restricted"},
            ),
        ]
    )

    service = RetrievalService(
        embedding_provider=embeddings,
        vector_store=vector_store,
    )

    request = RetrievalRequest(query="search", top_k=10)

    # 1. Analyst (clearance INTERNAL) sees PUBLIC and INTERNAL only
    analyst = _make_principal(Role.ANALYST)
    resp_analyst = await service.search(principal=analyst, request=request)
    assert {m.chunk_id for m in resp_analyst.matches} == {"chunk-pub", "chunk-int"}

    # 2. Manager (clearance CONFIDENTIAL) sees PUBLIC, INTERNAL, and CONFIDENTIAL
    manager = _make_principal(Role.MANAGER)
    resp_manager = await service.search(principal=manager, request=request)
    assert {m.chunk_id for m in resp_manager.matches} == {"chunk-pub", "chunk-int", "chunk-conf"}

    # 3. Admin (clearance RESTRICTED) sees all 4 levels
    admin = _make_principal(Role.ADMIN)
    resp_admin = await service.search(principal=admin, request=request)
    assert {m.chunk_id for m in resp_admin.matches} == {
        "chunk-pub",
        "chunk-int",
        "chunk-conf",
        "chunk-rest",
    }


async def test_search_tenant_isolation_enforced() -> None:
    embeddings = MockEmbeddingProvider()
    vector_store = InMemoryVectorStore()

    # Seed chunk for Org A and chunk for Org B
    await vector_store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-a",
                document_id=str(DOC_ID_1),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Org A confidential policy",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="chunk-b",
                document_id=str(DOC_ID_2),
                organization_id=str(ORG_B),
                embedding=[0.0] * 1024,
                content="Org B confidential policy",
                metadata={"confidentiality_level": "internal"},
            ),
        ]
    )

    service = RetrievalService(
        embedding_provider=embeddings,
        vector_store=vector_store,
    )

    request = RetrievalRequest(query="confidential policy", top_k=10)

    # Principal from Org A only gets Org A chunks
    principal_a = _make_principal(Role.ANALYST, organization_id=ORG_A)
    resp_a = await service.search(principal=principal_a, request=request)
    assert [m.chunk_id for m in resp_a.matches] == ["chunk-a"]

    # Principal from Org B only gets Org B chunks
    principal_b = _make_principal(Role.ANALYST, organization_id=ORG_B)
    resp_b = await service.search(principal=principal_b, request=request)
    assert [m.chunk_id for m in resp_b.matches] == ["chunk-b"]
