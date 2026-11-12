"""Unit tests for Amazon S3 Vectors adapter."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.adapters.aws.s3_vector_store import S3VectorStore
from app.core.config import AgentWorkerSettings, WorkerSettings
from app.core.container import (
    build_research_agent_worker_container,
    build_worker_container,
)
from app.core.errors import UpstreamError
from app.models.ai import VectorRecord
from app.models.enums import ConfidentialityLevel


class MockS3VectorsClient:
    """Mock boto3 s3vectors client for deterministic unit testing."""

    def __init__(self) -> None:
        self.put_vectors_calls: list[dict[str, Any]] = []
        self.query_vectors_calls: list[dict[str, Any]] = []
        self.list_vectors_calls: list[dict[str, Any]] = []
        self.delete_vectors_calls: list[dict[str, Any]] = []
        self.get_index_calls: list[dict[str, Any]] = []
        self.query_response: dict[str, Any] = {"vectors": []}
        self.list_response: dict[str, Any] = {"vectors": []}
        self.get_index_response: dict[str, Any] = {
            "index": {
                "indexArn": (
                    "arn:aws:s3vectors:us-east-1:123456789012:"
                    "bucket/test-vector-bucket/index/test-index"
                ),
                "indexName": "test-index",
                "vectorBucketName": "test-vector-bucket",
                "dimension": 1024,
                "distanceMetric": "cosine",
                "metadataConfiguration": {"nonFilterableMetadataKeys": []},
                "status": "AVAILABLE",
            }
        }
        self.should_fail: str | None = None

    def get_index(self, **kwargs: Any) -> dict[str, Any]:
        self.get_index_calls.append(kwargs)
        if self.should_fail == "get_index":
            raise RuntimeError("Underlying S3Vectors get_index error")
        return self.get_index_response

    def put_vectors(self, **kwargs: Any) -> dict[str, Any]:
        if self.should_fail == "put_vectors":
            raise RuntimeError("Underlying S3Vectors put error")
        self.put_vectors_calls.append(kwargs)
        return {}

    def query_vectors(self, **kwargs: Any) -> dict[str, Any]:
        if self.should_fail == "query_vectors":
            raise RuntimeError("Underlying S3Vectors query error")
        self.query_vectors_calls.append(kwargs)
        return self.query_response

    def list_vectors(self, **kwargs: Any) -> dict[str, Any]:
        if self.should_fail == "list_vectors":
            raise RuntimeError("Underlying S3Vectors list error")
        self.list_vectors_calls.append(kwargs)
        return self.list_response

    def delete_vectors(self, **kwargs: Any) -> dict[str, Any]:
        if self.should_fail == "delete_vectors":
            raise RuntimeError("Underlying S3Vectors delete error")
        self.delete_vectors_calls.append(kwargs)
        return {}


@pytest.fixture
def mock_client() -> MockS3VectorsClient:
    return MockS3VectorsClient()


@pytest.fixture
def store(mock_client: MockS3VectorsClient) -> S3VectorStore:
    return S3VectorStore(
        vector_bucket_name="test-vector-bucket",
        index_name="test-index",
        client=mock_client,
    )


def test_init_validation() -> None:
    with pytest.raises(ValueError, match="vector bucket name is required"):
        S3VectorStore(vector_bucket_name="", index_name="idx", client=MagicMock())

    with pytest.raises(ValueError, match="vector index name is required"):
        S3VectorStore(vector_bucket_name="bucket", index_name="   ", client=MagicMock())

    with pytest.raises(ValueError, match="dimension=1024"):
        S3VectorStore(
            vector_bucket_name="bucket",
            index_name="idx",
            dimension=512,
            client=MagicMock(),
        )

    with pytest.raises(ValueError, match="distance_metric='cosine'"):
        S3VectorStore(
            vector_bucket_name="bucket",
            index_name="idx",
            distance_metric="euclidean",
            client=MagicMock(),
        )


@pytest.mark.asyncio
async def test_upsert_validation(store: S3VectorStore, mock_client: MockS3VectorsClient) -> None:
    # Empty list returns 0 immediately without calling client
    assert await store.upsert([]) == 0
    assert len(mock_client.put_vectors_calls) == 0

    # Wrong dimensions
    bad_record = VectorRecord(
        chunk_id=str(uuid4()),
        document_id=str(uuid4()),
        organization_id=str(uuid4()),
        embedding=[0.1] * 512,  # Expected 1024
        content="Test content",
    )
    with pytest.raises(ValueError, match=r"Invalid chunk .* dimensions: expected 1024, got 512"):
        await store.upsert([bad_record])


@pytest.mark.asyncio
async def test_upsert_contradictory_metadata_rejection(store: S3VectorStore) -> None:
    org_id = str(uuid4())
    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    embedding = [0.1] * 1024

    # Contradictory org_id
    bad_org_record = VectorRecord(
        chunk_id=chunk_id,
        document_id=doc_id,
        organization_id=org_id,
        embedding=embedding,
        content="Test",
        metadata={"organization_id": str(uuid4())},
    )
    with pytest.raises(ValueError, match="Contradictory organization_id"):
        await store.upsert([bad_org_record])

    # Contradictory doc_id
    bad_doc_record = VectorRecord(
        chunk_id=chunk_id,
        document_id=doc_id,
        organization_id=org_id,
        embedding=embedding,
        content="Test",
        metadata={"document_id": str(uuid4())},
    )
    with pytest.raises(ValueError, match="Contradictory document_id"):
        await store.upsert([bad_doc_record])

    # Contradictory chunk_id
    bad_chunk_record = VectorRecord(
        chunk_id=chunk_id,
        document_id=doc_id,
        organization_id=org_id,
        embedding=embedding,
        content="Test",
        metadata={"chunk_id": str(uuid4())},
    )
    with pytest.raises(ValueError, match="Contradictory chunk_id"):
        await store.upsert([bad_chunk_record])


@pytest.mark.asyncio
async def test_upsert_success(store: S3VectorStore, mock_client: MockS3VectorsClient) -> None:
    org_id = str(uuid4())
    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    embedding = [0.01 * (i % 10) for i in range(1024)]

    record = VectorRecord(
        chunk_id=chunk_id,
        document_id=doc_id,
        organization_id=org_id,
        embedding=embedding,
        content="Section 4.1 Access Control Requirements",
        metadata={
            "confidentiality_level": ConfidentialityLevel.CONFIDENTIAL.value,
            "section": "4.1",
        },
    )

    written = await store.upsert([record])
    assert written == 1
    assert len(mock_client.put_vectors_calls) == 1

    call = mock_client.put_vectors_calls[0]
    assert call["vectorBucketName"] == "test-vector-bucket"
    assert call["indexName"] == "test-index"
    assert len(call["vectors"]) == 1

    vec = call["vectors"][0]
    assert vec["key"] == f"{org_id}#{doc_id}#{chunk_id}"
    assert len(vec["data"]["float32"]) == 1024
    assert vec["metadata"]["organization_id"] == org_id
    assert vec["metadata"]["document_id"] == doc_id
    assert vec["metadata"]["chunk_id"] == chunk_id
    assert vec["metadata"]["confidentiality_level"] == "confidential"
    assert vec["metadata"]["section"] == "4.1"
    assert vec["metadata"]["content"] == "Section 4.1 Access Control Requirements"


@pytest.mark.asyncio
async def test_search_input_validation(store: S3VectorStore) -> None:
    org_id = uuid4()
    clearances = (ConfidentialityLevel.INTERNAL,)

    # Invalid embedding
    with pytest.raises(ValueError, match="Invalid query embedding dimensions"):
        await store.search(
            embedding=[0.1] * 10,
            organization_id=org_id,
            confidentiality_levels=clearances,
        )

    # Invalid top_k
    valid_emb = [0.1] * 1024
    with pytest.raises(ValueError, match="top_k must be an integer between 1 and 100"):
        await store.search(
            embedding=valid_emb,
            organization_id=org_id,
            confidentiality_levels=clearances,
            top_k=0,
        )

    # Empty confidentiality_levels returns empty list without calling API
    res = await store.search(
        embedding=valid_emb,
        organization_id=org_id,
        confidentiality_levels=(),
    )
    assert res == []


@pytest.mark.asyncio
async def test_search_filter_and_defense_in_depth(
    store: S3VectorStore, mock_client: MockS3VectorsClient
) -> None:
    org_id = uuid4()
    foreign_org_id = uuid4()
    doc_id1 = uuid4()
    doc_id2 = uuid4()
    chunk_id1 = uuid4()
    chunk_id2 = uuid4()
    chunk_id3 = uuid4()

    valid_emb = [0.0] * 1024
    clearances = (ConfidentialityLevel.INTERNAL, ConfidentialityLevel.CONFIDENTIAL)

    mock_client.query_response = {
        "vectors": [
            {
                "key": f"{org_id}#{doc_id1}#{chunk_id1}",
                "distance": 0.15,
                "metadata": {
                    "organization_id": str(org_id),
                    "document_id": str(doc_id1),
                    "chunk_id": str(chunk_id1),
                    "confidentiality_level": "internal",
                    "content": "Valid authorized content",
                },
            },
            {
                "key": f"{foreign_org_id}#{doc_id2}#{chunk_id2}",
                "distance": 0.10,
                "metadata": {
                    "organization_id": str(foreign_org_id),
                    "document_id": str(doc_id2),
                    "chunk_id": str(chunk_id2),
                    "confidentiality_level": "internal",
                    "content": "Cross-tenant leak",
                },
            },
            {
                "key": f"{org_id}#{doc_id1}#{chunk_id3}",
                "distance": 0.05,
                "metadata": {
                    "organization_id": str(org_id),
                    "document_id": str(doc_id1),
                    "chunk_id": str(chunk_id3),
                    "confidentiality_level": "restricted",
                    "content": "Restricted content exceeding clearance",
                },
            },
        ]
    }

    results = await store.search(
        embedding=valid_emb,
        organization_id=org_id,
        confidentiality_levels=clearances,
        top_k=5,
        document_ids=[doc_id1],
    )

    assert len(mock_client.query_vectors_calls) == 1
    call = mock_client.query_vectors_calls[0]
    expected_filter = {
        "$and": [
            {"organization_id": {"$eq": str(org_id)}},
            {"confidentiality_level": {"$in": ["internal", "confidential"]}},
            {"document_id": {"$in": [str(doc_id1)]}},
        ]
    }
    assert call["filter"] == expected_filter
    assert call["topK"] == 5

    # Only vector 1 survived
    assert len(results) == 1
    match = results[0]
    assert match.chunk_id == str(chunk_id1)
    assert match.document_id == str(doc_id1)
    assert match.content == "Valid authorized content"
    assert pytest.approx(match.score, 0.001) == 0.85


@pytest.mark.asyncio
async def test_search_document_filter_violation_suppressed(
    store: S3VectorStore, mock_client: MockS3VectorsClient
) -> None:
    """If S3 Vectors returns a document not in document_ids, suppress it."""
    org_id = uuid4()
    doc_id1 = uuid4()
    doc_id2 = uuid4()  # Not requested
    chunk_id = uuid4()

    mock_client.query_response = {
        "vectors": [
            {
                "key": f"{org_id}#{doc_id2}#{chunk_id}",
                "distance": 0.20,
                "metadata": {
                    "organization_id": str(org_id),
                    "document_id": str(doc_id2),
                    "chunk_id": str(chunk_id),
                    "confidentiality_level": "internal",
                    "content": "Unrequested document content",
                },
            }
        ]
    }

    results = await store.search(
        embedding=[0.0] * 1024,
        organization_id=org_id,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
        document_ids=[doc_id1],
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_contradictory_key_and_metadata_suppression(
    store: S3VectorStore, mock_client: MockS3VectorsClient
) -> None:
    """Contradictory key vs metadata IDs must be suppressed."""
    org_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    # Contradictory document_id
    mock_client.query_response = {
        "vectors": [
            {
                "key": f"{org_id}#{doc_id}#{chunk_id}",
                "distance": 0.10,
                "metadata": {
                    "organization_id": str(org_id),
                    "document_id": str(uuid4()),  # Differs from key!
                    "chunk_id": str(chunk_id),
                    "confidentiality_level": "internal",
                    "content": "Contradictory doc ID",
                },
            }
        ]
    }

    results = await store.search(
        embedding=[0.0] * 1024,
        organization_id=org_id,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_malformed_and_nonfinite_distances(
    store: S3VectorStore, mock_client: MockS3VectorsClient
) -> None:
    """Nonfinite, negative, missing, or malformed distances must be suppressed, not scored 1.0."""
    org_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    bad_distances = [None, "invalid", float("nan"), float("inf"), float("-inf"), -0.5]

    for bad_dist in bad_distances:
        mock_client.query_response = {
            "vectors": [
                {
                    "key": f"{org_id}#{doc_id}#{chunk_id}",
                    "distance": bad_dist,
                    "metadata": {
                        "organization_id": str(org_id),
                        "document_id": str(doc_id),
                        "chunk_id": str(chunk_id),
                        "confidentiality_level": "internal",
                        "content": "Test content",
                    },
                }
            ]
        }
        results = await store.search(
            embedding=[0.0] * 1024,
            organization_id=org_id,
            confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
        )
        assert len(results) == 0, f"Expected suppression for distance={bad_dist}"

    # Positive control: valid distance 0.35 yields similarity score 0.65
    mock_client.query_response = {
        "vectors": [
            {
                "key": f"{org_id}#{doc_id}#{chunk_id}",
                "distance": 0.35,
                "metadata": {
                    "organization_id": str(org_id),
                    "document_id": str(doc_id),
                    "chunk_id": str(chunk_id),
                    "confidentiality_level": "internal",
                    "content": "Valid distance content",
                },
            }
        ]
    }
    results = await store.search(
        embedding=[0.0] * 1024,
        organization_id=org_id,
        confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
    )
    assert len(results) == 1
    assert pytest.approx(results[0].score, 0.001) == 0.65


@pytest.mark.asyncio
async def test_search_malformed_response_types(
    store: S3VectorStore, mock_client: MockS3VectorsClient
) -> None:
    """Non-dict items or non-list vector payloads must not crash search."""
    org_id = uuid4()

    mock_client.query_response = {"vectors": "not-a-list"}  # type: ignore[dict-item]
    assert (
        await store.search(
            embedding=[0.0] * 1024,
            organization_id=org_id,
            confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
        )
        == []
    )

    mock_client.query_response = {"vectors": ["not-a-dict", None, 123]}  # type: ignore[list-item]
    assert (
        await store.search(
            embedding=[0.0] * 1024,
            organization_id=org_id,
            confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
        )
        == []
    )


@pytest.mark.asyncio
async def test_delete_by_document(store: S3VectorStore, mock_client: MockS3VectorsClient) -> None:
    org_id = uuid4()
    doc_id = uuid4()
    other_doc_id = uuid4()

    mock_client.list_response = {
        "vectors": [
            {
                "key": f"{org_id}#{doc_id}#chunk-1",
                "metadata": {"organization_id": str(org_id), "document_id": str(doc_id)},
            },
            {
                "key": f"{org_id}#{doc_id}#chunk-2",
                "metadata": {"organization_id": str(org_id), "document_id": str(doc_id)},
            },
            {
                "key": f"{org_id}#{other_doc_id}#chunk-3",
                "metadata": {"organization_id": str(org_id), "document_id": str(other_doc_id)},
            },
        ]
    }

    deleted = await store.delete_by_document(document_id=doc_id, organization_id=org_id)
    assert deleted == 2
    assert len(mock_client.delete_vectors_calls) == 1
    del_call = mock_client.delete_vectors_calls[0]
    assert del_call["keys"] == [f"{org_id}#{doc_id}#chunk-1", f"{org_id}#{doc_id}#chunk-2"]


@pytest.mark.asyncio
async def test_delete_refuses_foreign_tenant_key_even_with_matching_metadata(
    store: S3VectorStore, mock_client: MockS3VectorsClient
) -> None:
    """A foreign tenant's key must NEVER be deleted based on metadata alone."""
    org_id = uuid4()
    foreign_org_id = uuid4()
    doc_id = uuid4()

    mock_client.list_response = {
        "vectors": [
            {
                # Foreign key with spoofed metadata!
                "key": f"{foreign_org_id}#{doc_id}#chunk-1",
                "metadata": {"organization_id": str(org_id), "document_id": str(doc_id)},
            },
            {
                # Valid key matching prefix and metadata
                "key": f"{org_id}#{doc_id}#chunk-2",
                "metadata": {"organization_id": str(org_id), "document_id": str(doc_id)},
            },
            {
                # Key matching prefix but contradictory metadata
                "key": f"{org_id}#{doc_id}#chunk-3",
                "metadata": {
                    "organization_id": str(foreign_org_id),
                    "document_id": str(doc_id),
                },
            },
        ]
    }

    deleted = await store.delete_by_document(document_id=doc_id, organization_id=org_id)
    assert deleted == 1
    assert len(mock_client.delete_vectors_calls) == 1
    assert mock_client.delete_vectors_calls[0]["keys"] == [f"{org_id}#{doc_id}#chunk-2"]


@pytest.mark.asyncio
async def test_upstream_error_propagation(
    store: S3VectorStore, mock_client: MockS3VectorsClient
) -> None:
    # Upsert error
    mock_client.should_fail = "put_vectors"
    record = VectorRecord(
        chunk_id=str(uuid4()),
        document_id=str(uuid4()),
        organization_id=str(uuid4()),
        embedding=[0.0] * 1024,
        content="Test",
    )
    with pytest.raises(UpstreamError, match=r"Could not access vector persistence\."):
        await store.upsert([record])

    # Search error
    mock_client.should_fail = "query_vectors"
    with pytest.raises(UpstreamError, match=r"Could not access vector persistence\."):
        await store.search(
            embedding=[0.0] * 1024,
            organization_id=uuid4(),
            confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
        )

    # List error
    mock_client.should_fail = "list_vectors"
    with pytest.raises(UpstreamError, match=r"Could not access vector persistence\."):
        await store.delete_by_document(document_id=uuid4(), organization_id=uuid4())

    # Delete error
    mock_client.should_fail = "delete_vectors"
    target_org = uuid4()
    target_doc = uuid4()
    mock_client.list_response = {
        "vectors": [
            {
                "key": f"{target_org}#{target_doc}#chunk1",
                "metadata": {"organization_id": str(target_org), "document_id": str(target_doc)},
            }
        ]
    }
    with pytest.raises(UpstreamError, match=r"Could not access vector persistence\."):
        await store.delete_by_document(document_id=target_doc, organization_id=target_org)


def test_init_with_index_arn() -> None:
    client = MagicMock()
    # Valid ARN
    valid_arn = "arn:aws:s3vectors:us-east-1:123456789012:bucket/my-bucket/index/my-index"
    s = S3VectorStore(index_arn=valid_arn, client=client)
    assert s._vector_bucket_name == "my-bucket"
    assert s._index_name == "my-index"

    # Malformed ARNs
    with pytest.raises(ValueError, match="Malformed S3 Vectors index ARN"):
        S3VectorStore(
            index_arn="arn:aws:s3vectors:us-east-1:123456789012:index/my-index",
            client=client,
        )

    with pytest.raises(ValueError, match="Malformed S3 Vectors index ARN"):
        S3VectorStore(index_arn="not-an-arn", client=client)


@pytest.mark.asyncio
async def test_validate_index_contract(
    store: S3VectorStore,
    mock_client: MockS3VectorsClient,
) -> None:
    # 1. Matching SDK contract succeeds
    resp = await store.validate_index_contract()
    assert resp["index"]["dimension"] == 1024
    assert resp["index"]["distanceMetric"] == "cosine"

    # 2. Dimension mismatch (512) raises ValueError
    mock_client.get_index_response = {
        "index": {"dimension": 512, "distanceMetric": "cosine"}
    }
    with pytest.raises(
        ValueError,
        match="S3 Vectors index dimension mismatch: expected 1024, got 512",
    ):
        await store.validate_index_contract()

    # 3. Metric mismatch (euclidean) raises ValueError
    mock_client.get_index_response = {
        "index": {"dimension": 1024, "distanceMetric": "euclidean"}
    }
    with pytest.raises(
        ValueError,
        match="S3 Vectors index distance metric mismatch: expected 'cosine', got 'euclidean'",
    ):
        await store.validate_index_contract()

    # 4. Upstream error raises UpstreamError
    mock_client.should_fail = "get_index"
    with pytest.raises(UpstreamError, match="Could not access vector persistence"):
        await store.validate_index_contract()


@pytest.mark.asyncio
async def test_validate_index_contract_negative_controls(
    store: S3VectorStore,
    mock_client: MockS3VectorsClient,
) -> None:
    """Negative controls proving missing index, missing attributes, or bad config fail."""
    # 1. Missing index object entirely
    mock_client.get_index_response = {"status": "AVAILABLE"}
    with pytest.raises(ValueError, match="missing required 'index' object"):
        await store.validate_index_contract()

    # 2. Missing dimension attribute
    mock_client.get_index_response = {"index": {"distanceMetric": "cosine"}}
    with pytest.raises(ValueError, match="missing required 'dimension' attribute"):
        await store.validate_index_contract()

    # 3. Missing distanceMetric attribute
    mock_client.get_index_response = {"index": {"dimension": 1024}}
    with pytest.raises(ValueError, match="missing required 'distanceMetric' attribute"):
        await store.validate_index_contract()

    # 4. Incompatible metadataConfiguration: nonFilterableMetadataKeys must be a list
    mock_client.get_index_response = {
        "index": {
            "dimension": 1024,
            "distanceMetric": "cosine",
            "metadataConfiguration": {
                "nonFilterableMetadataKeys": "not-a-list",
            },
        }
    }
    with pytest.raises(
        ValueError,
        match="nonFilterableMetadataKeys must be a list",
    ):
        await store.validate_index_contract()

    # 5. Incompatible metadataConfiguration: list items must be str
    mock_client.get_index_response = {
        "index": {
            "dimension": 1024,
            "distanceMetric": "cosine",
            "metadataConfiguration": {
                "nonFilterableMetadataKeys": [123],
            },
        }
    }
    with pytest.raises(
        ValueError,
        match="nonFilterableMetadataKeys item must be str",
    ):
        await store.validate_index_contract()

    # 6. Prove each conflicting key fails individually
    for conflict_key in ("organization_id", "confidentiality_level", "document_id"):
        mock_client.get_index_response = {
            "index": {
                "dimension": 1024,
                "distanceMetric": "cosine",
                "metadataConfiguration": {
                    "nonFilterableMetadataKeys": [conflict_key],
                },
            }
        }
        with pytest.raises(
            ValueError,
            match=f"Incompatible S3 Vectors metadataConfiguration: \\['{conflict_key}'\\]",
        ):
            await store.validate_index_contract()

    # 7. Positive control: valid non-filterable content metadata passes
    mock_client.get_index_response = {
        "index": {
            "dimension": 1024,
            "distanceMetric": "cosine",
            "metadataConfiguration": {
                "nonFilterableMetadataKeys": ["content", "section", "doc_title"],
            },
        }
    }
    resp = await store.validate_index_contract()
    meta_cfg = resp["index"]["metadataConfiguration"]
    assert meta_cfg["nonFilterableMetadataKeys"] == ["content", "section", "doc_title"]


@pytest.mark.asyncio
async def test_validation_occurs_before_data_operations(
    mock_client: MockS3VectorsClient,
) -> None:
    """Data operations must validate contract before executing remote operations."""
    store = S3VectorStore(
        vector_bucket_name="test-bucket",
        index_name="test-index",
        client=mock_client,
    )
    assert len(mock_client.get_index_calls) == 0

    record = VectorRecord(
        chunk_id=str(uuid4()),
        document_id=str(uuid4()),
        organization_id=str(uuid4()),
        embedding=[0.01 * (i % 10) for i in range(1024)],
        content="Test content",
    )

    # First data operation (upsert) executes get_index first
    written = await store.upsert([record])
    assert written == 1
    assert len(mock_client.get_index_calls) == 1
    assert len(mock_client.put_vectors_calls) == 1

    # Subsequent operation reuses validated state without duplicate get_index calls
    await store.search(
        embedding=[0.0] * 1024,
        organization_id=uuid4(),
        confidentiality_levels=(ConfidentialityLevel.INTERNAL,),
    )
    assert len(mock_client.get_index_calls) == 1


@pytest.mark.asyncio
async def test_data_operation_aborts_if_contract_fails(
    mock_client: MockS3VectorsClient,
) -> None:
    """If index contract validation fails, no data mutation occurs."""
    mock_client.get_index_response = {
        "index": {"dimension": 512, "distanceMetric": "cosine"}
    }
    store = S3VectorStore(
        vector_bucket_name="test-bucket",
        index_name="test-index",
        client=mock_client,
    )

    record = VectorRecord(
        chunk_id=str(uuid4()),
        document_id=str(uuid4()),
        organization_id=str(uuid4()),
        embedding=[0.0] * 1024,
        content="Test",
    )
    with pytest.raises(ValueError, match="dimension mismatch"):
        await store.upsert([record])

    # Put vectors was NEVER called
    assert len(mock_client.put_vectors_calls) == 0


@pytest.mark.asyncio
async def test_delete_by_document_malformed_responses(
    store: S3VectorStore, mock_client: MockS3VectorsClient
) -> None:
    target_org = uuid4()
    target_doc = uuid4()

    # 1. Non-dict listing response
    mock_client.list_response = "not-a-dict"  # type: ignore[assignment]
    with pytest.raises(
        UpstreamError,
        match="Could not list vectors for deletion: malformed response",
    ):
        await store.delete_by_document(document_id=target_doc, organization_id=target_org)

    # 2. Non-list vectors payload
    mock_client.list_response = {"vectors": "not-a-list"}
    with pytest.raises(UpstreamError, match="vectors payload is not a list"):
        await store.delete_by_document(document_id=target_doc, organization_id=target_org)

    # 3. Non-dict vector item
    mock_client.list_response = {"vectors": ["not-a-dict"]}
    with pytest.raises(UpstreamError, match="vector item is not a dictionary"):
        await store.delete_by_document(document_id=target_doc, organization_id=target_org)

    # 4. Non-string nextToken
    mock_client.list_response = {"vectors": [], "nextToken": 12345}
    with pytest.raises(UpstreamError, match="nextToken is not a string"):
        await store.delete_by_document(document_id=target_doc, organization_id=target_org)

    # 5. Non-dict metadata inside vector does not crash and safely filters
    mock_client.list_response = {
        "vectors": [
            {
                "key": f"{target_org}#{target_doc}#chunk1",
                "metadata": "not-a-dict-metadata",
            }
        ]
    }
    deleted = await store.delete_by_document(document_id=target_doc, organization_id=target_org)
    assert deleted == 1


def test_worker_startup_with_synthesized_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup test using the exact environment synthesized for both workers."""
    # 1. Exact synthesized environment for document_worker (IngestionStack)
    doc_env = {
        "ENVIRONMENT": "dev",
        "RATE_LIMIT_TABLE_NAME": "cloudguard-admission-dev",
        "AWS_AURORA_CLUSTER_ARN": "arn:aws:rds:us-east-1:123456789012:cluster:cloudguard-dev",
        "AWS_AURORA_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:db-creds",
        "DB_NAME": "cloudguard",
        "DOCUMENT_STORE": "s3",
        "AWS_DOCUMENTS_BUCKET": "cloudguard-documents-dev-123456789012",
        "EVENT_PUBLISHER": "eventbridge",
        "AWS_EVENT_BUS_NAME": "cloudguard-events",
        "LLM_PROVIDER": "bedrock",
        "BEDROCK_EMBEDDING_MODEL": "amazon.titan-embed-text-v2:0",
        "VECTOR_STORE": "s3_vectors",
        "AWS_S3_VECTOR_INDEX_ARN": (
            "arn:aws:s3vectors:us-east-1:123456789012:bucket/cloudguard-vector-bucket/index/cloudguard-vector-index"
        ),
        "AWS_S3_VECTOR_BUCKET_NAME": "cloudguard-vector-bucket",
        "AWS_S3_VECTOR_INDEX_NAME": "cloudguard-vector-index",
    }
    for k, v in doc_env.items():
        monkeypatch.setenv(k, v)

    worker_settings = WorkerSettings()
    doc_container = build_worker_container(worker_settings)
    assert isinstance(doc_container.vectors, S3VectorStore)
    assert doc_container.vectors._vector_bucket_name == "cloudguard-vector-bucket"
    assert doc_container.vectors._index_name == "cloudguard-vector-index"

    # 2. Exact synthesized environment for research_agent_worker (AgentWorkflowStack)
    research_env = {
        "ENVIRONMENT": "dev",
        "LLM_PROVIDER": "bedrock",
        "ENABLE_AGENTIC_WORKFLOWS": "false",
        "VECTOR_STORE": "s3_vectors",
        "AWS_AURORA_CLUSTER_ARN": "arn:aws:rds:us-east-1:123456789012:cluster:cloudguard-dev",
        "AWS_AURORA_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:db-creds",
        "DB_NAME": "cloudguard",
        "BEDROCK_CHAT_MODEL": "anthropic.claude-haiku-4-5-20251001-v1:0",
        "BEDROCK_EMBEDDING_MODEL": "amazon.titan-embed-text-v2:0",
        "RATE_LIMIT_TABLE_NAME": "cloudguard-admission-dev",
        "AWS_S3_VECTOR_INDEX_ARN": (
            "arn:aws:s3vectors:us-east-1:123456789012:bucket/cloudguard-vector-bucket/index/cloudguard-vector-index"
        ),
        "AWS_S3_VECTOR_BUCKET_NAME": "cloudguard-vector-bucket",
        "AWS_S3_VECTOR_INDEX_NAME": "cloudguard-vector-index",
    }
    for k, v in research_env.items():
        monkeypatch.setenv(k, v)

    agent_settings = AgentWorkerSettings()
    agent_container = build_research_agent_worker_container(agent_settings)
    assert isinstance(agent_container.vectors, S3VectorStore)
    assert agent_container.vectors._vector_bucket_name == "cloudguard-vector-bucket"
    assert agent_container.vectors._index_name == "cloudguard-vector-index"
