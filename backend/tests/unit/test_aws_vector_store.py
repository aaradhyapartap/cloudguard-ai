"""Unit tests for AuroraDataAPIVectorStore."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from app.adapters.aws.vector_store import AuroraDataAPIVectorStore
from app.core.errors import UpstreamError
from app.models.ai import VectorRecord
from app.models.enums import ConfidentialityLevel


class FakeRDSDataClient:
    """In-memory RDS Data API client double for unit tests."""

    def __init__(self) -> None:
        self.statements: list[dict[str, Any]] = []
        self.transactions_begun: list[dict[str, Any]] = []
        self.transactions_committed: list[str] = []
        self.transactions_rolled_back: list[str] = []
        self.search_records_to_return: list[list[dict[str, Any]]] = []
        self.execute_should_fail: bool = False
        # Map of (org_id, doc_id, chunk_id) to updated status
        self.existing_chunks: set[tuple[str, str, str]] = set()

    def begin_transaction(self, **kwargs: Any) -> dict[str, Any]:
        self.transactions_begun.append(kwargs)
        return {"transactionId": f"tx-{len(self.transactions_begun)}"}

    def commit_transaction(self, **kwargs: Any) -> dict[str, Any]:
        self.transactions_committed.append(str(kwargs.get("transactionId")))
        return {}

    def rollback_transaction(self, **kwargs: Any) -> dict[str, Any]:
        self.transactions_rolled_back.append(str(kwargs.get("transactionId")))
        return {}

    def execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        self.statements.append(kwargs)
        if self.execute_should_fail:
            raise RuntimeError("Database connection reset")
        sql = str(kwargs.get("sql", ""))
        parameters = kwargs.get("parameters", [])

        if "SELECT set_config" in sql:
            return {"records": []}

        if "UPDATE document_chunks" in sql and "SET embedding = NULL" in sql:
            return {"numberOfRecordsUpdated": 3}

        if "UPDATE document_chunks" in sql and "SET embedding = CAST(:embedding AS vector)" in sql:
            params_dict = {
                p["name"]: p["value"]["stringValue"]
                for p in parameters
                if "stringValue" in p.get("value", {})
            }
            key = (
                params_dict.get("organization_id", ""),
                params_dict.get("document_id", ""),
                params_dict.get("chunk_id", ""),
            )
            if key in self.existing_chunks:
                return {"numberOfRecordsUpdated": 1}
            return {"numberOfRecordsUpdated": 0}

        if "SELECT c.id, c.document_id" in sql:
            return {"records": self.search_records_to_return}

        return {"records": []}


@pytest.fixture
def fake_client() -> FakeRDSDataClient:
    return FakeRDSDataClient()


@pytest.fixture
def vector_store(fake_client: FakeRDSDataClient) -> AuroraDataAPIVectorStore:
    return AuroraDataAPIVectorStore(
        resource_arn="arn:aws:rds:us-east-1:123456789012:cluster:cloudguard-test",
        secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:db-creds",
        database="cloudguard",
        region="us-east-1",
        client=fake_client,
    )


@pytest.mark.parametrize("invalid_vec", [
    [0.1] * 1023,
    [0.1] * 1025,
    [float("nan")] * 1024,
    [float("inf")] * 1024,
    [float("-inf")] * 1024,
])
async def test_upsert_validates_dimensions_and_finite_numbers(
    vector_store: AuroraDataAPIVectorStore,
    invalid_vec: list[Any],
) -> None:
    invalid_record = VectorRecord(
        chunk_id=str(uuid4()),
        document_id=str(uuid4()),
        organization_id=str(uuid4()),
        embedding=invalid_vec,
        content="Test content",
    )
    with pytest.raises(ValueError, match="Invalid chunk"):
        await vector_store.upsert([invalid_record])


async def test_upsert_executes_row_scoped_update_with_vector_cast(
    vector_store: AuroraDataAPIVectorStore,
    fake_client: FakeRDSDataClient,
) -> None:
    org_id = uuid4()
    doc_id = uuid4()
    chunk1_id = uuid4()
    chunk2_id = uuid4()

    fake_client.existing_chunks.add((str(org_id), str(doc_id), str(chunk1_id)))
    fake_client.existing_chunks.add((str(org_id), str(doc_id), str(chunk2_id)))

    records = [
        VectorRecord(
            chunk_id=str(chunk1_id),
            document_id=str(doc_id),
            organization_id=str(org_id),
            embedding=[0.05] * 1024,
            content="Chunk 1",
        ),
        VectorRecord(
            chunk_id=str(chunk2_id),
            document_id=str(doc_id),
            organization_id=str(org_id),
            embedding=[-0.05] * 1024,
            content="Chunk 2",
        ),
    ]

    updated = await vector_store.upsert(records)
    assert updated == 2

    # Verify tenant transaction lifecycle
    assert len(fake_client.transactions_begun) == 1
    assert len(fake_client.transactions_committed) == 1
    assert len(fake_client.transactions_rolled_back) == 0

    # Verify update statements have full identity: organization_id, document_id, chunk_id
    update_stmts = [
        s for s in fake_client.statements
        if "SET embedding = CAST(:embedding AS vector)" in str(s.get("sql"))
    ]
    assert len(update_stmts) == 2
    sql = str(update_stmts[0]["sql"])
    assert "WHERE organization_id = :organization_id" in sql
    assert "AND document_id = :document_id" in sql
    assert "AND id = :chunk_id" in sql

    params = update_stmts[0]["parameters"]
    assert any(
        p["name"] == "organization_id" and p["value"]["stringValue"] == str(org_id)
        for p in params
    )
    assert any(
        p["name"] == "document_id" and p["value"]["stringValue"] == str(doc_id)
        for p in params
    )
    assert any(
        p["name"] == "chunk_id" and p["value"]["stringValue"] == str(chunk1_id)
        for p in params
    )
    assert any(
        p["name"] == "embedding" and p["value"]["stringValue"].startswith("[0.05,0.05,")
        for p in params
    )


async def test_upsert_does_not_count_nonexistent_or_wrong_document_chunk(
    vector_store: AuroraDataAPIVectorStore,
    fake_client: FakeRDSDataClient,
) -> None:
    org_id = uuid4()
    correct_doc_id = uuid4()
    wrong_doc_id = uuid4()
    chunk1_id = uuid4()
    chunk2_id = uuid4()

    # Only chunk1 exists under correct_doc_id
    fake_client.existing_chunks.add((str(org_id), str(correct_doc_id), str(chunk1_id)))

    records = [
        # Match
        VectorRecord(
            chunk_id=str(chunk1_id),
            document_id=str(correct_doc_id),
            organization_id=str(org_id),
            embedding=[0.05] * 1024,
            content="Chunk 1",
        ),
        # Wrong document_id: must not count
        VectorRecord(
            chunk_id=str(chunk1_id),
            document_id=str(wrong_doc_id),
            organization_id=str(org_id),
            embedding=[0.05] * 1024,
            content="Chunk 1 wrong doc",
        ),
        # Nonexistent chunk: must not count
        VectorRecord(
            chunk_id=str(chunk2_id),
            document_id=str(correct_doc_id),
            organization_id=str(org_id),
            embedding=[0.05] * 1024,
            content="Chunk 2 nonexistent",
        ),
    ]

    updated = await vector_store.upsert(records)
    assert updated == 1  # Exactly 1 row updated


@pytest.mark.parametrize("invalid_top_k", [0, -1, 101])
async def test_search_validates_top_k_bounds(
    vector_store: AuroraDataAPIVectorStore,
    invalid_top_k: int,
) -> None:
    org_id = uuid4()
    with pytest.raises(ValueError, match="top_k must be an integer between"):
        await vector_store.search(
            embedding=[0.1] * 1024,
            organization_id=org_id,
            confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
            top_k=invalid_top_k,
        )


async def test_search_returns_empty_when_no_confidentiality_levels(
    vector_store: AuroraDataAPIVectorStore,
    fake_client: FakeRDSDataClient,
) -> None:
    results = await vector_store.search(
        embedding=[0.1] * 1024,
        organization_id=uuid4(),
        confidentiality_levels=(),
    )
    assert results == []
    assert len(fake_client.statements) == 0


async def test_search_executes_cosine_similarity_query(
    vector_store: AuroraDataAPIVectorStore,
    fake_client: FakeRDSDataClient,
) -> None:
    org_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    fake_client.search_records_to_return = [
        [
            {"stringValue": str(chunk_id)},
            {"stringValue": str(doc_id)},
            {"stringValue": "Relevant policy section"},
            {"stringValue": json.dumps({"page": 2, "filename": "policy.pdf"})},
            {"doubleValue": 0.884},
        ]
    ]

    matches = await vector_store.search(
        embedding=[0.05] * 1024,
        organization_id=org_id,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL, ConfidentialityLevel.CONFIDENTIAL),
        top_k=5,
        document_ids=[doc_id],
    )

    assert len(matches) == 1
    match = matches[0]
    assert match.chunk_id == str(chunk_id)
    assert match.document_id == str(doc_id)
    assert match.content == "Relevant policy section"
    assert match.score == 0.884
    assert match.metadata["page"] == 2
    assert not hasattr(match, "embedding")

    # Verify search query SQL
    search_stmts = [
        s for s in fake_client.statements if "SELECT c.id, c.document_id" in str(s.get("sql"))
    ]
    assert len(search_stmts) == 1
    sql = str(search_stmts[0]["sql"])
    assert "1 - (c.embedding <=> CAST(:query_embedding AS vector)) AS score" in sql
    assert "c.embedding IS NOT NULL" in sql
    assert "CAST(d.confidentiality_level AS text) = ANY(:confidentiality_levels)" in sql
    assert "c.document_id = ANY(CAST(:document_ids AS uuid[]))" in sql
    assert "ORDER BY c.embedding <=> CAST(:query_embedding AS vector) ASC" in sql
    assert "LIMIT :top_k" in sql


async def test_delete_by_document_clears_embeddings_without_deleting_chunks(
    vector_store: AuroraDataAPIVectorStore,
    fake_client: FakeRDSDataClient,
) -> None:
    org_id = uuid4()
    doc_id = uuid4()

    cleared = await vector_store.delete_by_document(
        document_id=doc_id,
        organization_id=org_id,
    )
    assert cleared == 3

    del_stmts = [
        s for s in fake_client.statements if "SET embedding = NULL" in str(s.get("sql"))
    ]
    assert len(del_stmts) == 1
    sql = str(del_stmts[0]["sql"])
    assert "UPDATE document_chunks" in sql
    assert "SET embedding = NULL" in sql
    assert "WHERE organization_id = :organization_id" in sql
    assert "AND document_id = :document_id" in sql
    assert "AND embedding IS NOT NULL" in sql


async def test_transaction_rollback_on_provider_error(
    vector_store: AuroraDataAPIVectorStore,
    fake_client: FakeRDSDataClient,
) -> None:
    fake_client.execute_should_fail = True
    org_id = uuid4()
    doc_id = uuid4()

    with pytest.raises(UpstreamError):
        await vector_store.delete_by_document(
            document_id=doc_id,
            organization_id=org_id,
        )

    assert len(fake_client.transactions_begun) == 1
    assert len(fake_client.transactions_committed) == 0
    assert len(fake_client.transactions_rolled_back) == 1
