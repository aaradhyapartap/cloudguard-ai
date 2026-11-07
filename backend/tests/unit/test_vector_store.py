"""Unit tests for VectorStore port, models, validation helpers, and container configuration."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from app.adapters.aws.vector_store import (
    AuroraDataAPIVectorStore,
    _serialize_vector,
)
from app.adapters.local.vector_store import SQLAlchemyVectorStore
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.core.config import AWSSettings, DatabaseSettings, Environment, Settings
from app.core.container import _build_vector_store
from app.models.ai import VectorMatch, VectorRecord
from app.ports.vector_store import (
    EXPECTED_EMBEDDING_DIMENSIONS,
    MAX_TOP_K,
    MIN_TOP_K,
    validate_embedding,
    validate_top_k,
)
from app.repositories.tables import DocumentChunk


def test_document_chunk_has_vector_embedding_column() -> None:
    """DocumentChunk ORM model must define embedding as Vector(1024)."""
    assert hasattr(DocumentChunk, "embedding")
    col = DocumentChunk.__table__.columns["embedding"]
    assert str(col.type).lower().startswith("vector(1024)")
    assert col.nullable is True


def test_serialize_vector_formats_postgres_vector_literal() -> None:
    """Vector must serialize to PostgreSQL vector format '[x,y,z]'."""
    vec = [0.1, -0.25, 0.5]
    serialized = _serialize_vector(vec)
    assert serialized == "[0.1,-0.25,0.5]"


def test_vector_record_and_match_models() -> None:
    """VectorRecord and VectorMatch must hold provider-neutral metadata."""
    chunk_id = str(uuid4())
    doc_id = str(uuid4())
    org_id = str(uuid4())
    embedding = [0.0] * 1024

    record = VectorRecord(
        chunk_id=chunk_id,
        document_id=doc_id,
        organization_id=org_id,
        embedding=embedding,
        content="Test content",
        metadata={"confidentiality_level": "internal"},
    )
    assert record.chunk_id == chunk_id
    assert len(record.embedding) == 1024

    match = VectorMatch(
        chunk_id=chunk_id,
        document_id=doc_id,
        content="Test content",
        score=0.95,
        metadata={"confidentiality_level": "internal"},
    )
    assert match.score == 0.95
    assert not hasattr(match, "embedding")  # VectorMatch must not contain raw vector


def test_validate_embedding_accepts_valid_1024_float_vector() -> None:
    valid_vec = [0.01] * EXPECTED_EMBEDDING_DIMENSIONS
    validate_embedding(valid_vec)


@pytest.mark.parametrize("length", [1023, 1025, 0, 512])
def test_validate_embedding_rejects_invalid_dimensions(length: int) -> None:
    vec = [0.1] * length
    with pytest.raises(ValueError, match="expected 1024"):
        validate_embedding(vec)


def test_validate_embedding_rejects_nan() -> None:
    vec = [0.1] * 1024
    vec[42] = float("nan")
    with pytest.raises(ValueError, match="must be a finite numeric value"):
        validate_embedding(vec)


def test_validate_embedding_rejects_positive_infinity() -> None:
    vec = [0.1] * 1024
    vec[100] = float("inf")
    with pytest.raises(ValueError, match="must be a finite numeric value"):
        validate_embedding(vec)


def test_validate_embedding_rejects_negative_infinity() -> None:
    vec = [0.1] * 1024
    vec[100] = float("-inf")
    with pytest.raises(ValueError, match="must be a finite numeric value"):
        validate_embedding(vec)


def test_validate_embedding_rejects_bool() -> None:
    vec: list[Any] = [0.1] * 1024
    vec[10] = True
    with pytest.raises(ValueError, match="must be a finite numeric value"):
        validate_embedding(vec)


def test_validate_embedding_rejects_non_numeric() -> None:
    vec: list[Any] = [0.1] * 1024
    vec[10] = "invalid"
    with pytest.raises(ValueError, match="must be a finite numeric value"):
        validate_embedding(vec)


def test_validate_top_k_bounds() -> None:
    # Valid boundaries
    validate_top_k(MIN_TOP_K)
    validate_top_k(MAX_TOP_K)
    validate_top_k(10)

    # Invalid: non-positive
    with pytest.raises(ValueError, match="top_k must be an integer"):
        validate_top_k(0)
    with pytest.raises(ValueError, match="top_k must be an integer"):
        validate_top_k(-5)

    # Invalid: above maximum
    with pytest.raises(ValueError, match="top_k must be an integer"):
        validate_top_k(MAX_TOP_K + 1)

    # Invalid: bool
    with pytest.raises(ValueError, match="top_k must be an integer"):
        validate_top_k(True)  # type: ignore[arg-type]


def test_container_builds_in_memory_vector_store_when_configured() -> None:
    settings = Settings(
        vector_store="memory",
        environment=Environment.LOCAL,
    )
    store = _build_vector_store(settings)
    assert isinstance(store, InMemoryVectorStore)


def test_container_builds_sqlalchemy_vector_store_in_local_environment() -> None:
    settings = Settings(
        vector_store="pgvector",
        environment=Environment.LOCAL,
    )
    store = _build_vector_store(settings)
    assert isinstance(store, SQLAlchemyVectorStore)


def test_container_builds_aurora_data_api_vector_store_in_deployed_environment() -> None:
    settings = Settings(
        vector_store="pgvector",
        environment=Environment.DEV,
        identity_provider="cognito",
        cognito={"user_pool_id": "us-east-1_mock", "client_id": "mock_client"},
        aws=AWSSettings(
            aurora_cluster_arn="arn:aws:rds:us-east-1:123456789012:cluster:cloudguard-dev",
            aurora_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:db-creds-123456",
        ),
        database=DatabaseSettings(name="cloudguard"),
    )
    store = _build_vector_store(settings)
    assert isinstance(store, AuroraDataAPIVectorStore)


def test_container_fails_if_deployed_pgvector_missing_arns() -> None:
    settings = Settings(
        vector_store="pgvector",
        environment=Environment.DEV,
        identity_provider="cognito",
        cognito={"user_pool_id": "us-east-1_mock", "client_id": "mock_client"},
        aws=AWSSettings(
            aurora_cluster_arn=None,
            aurora_secret_arn=None,
        ),
    )
    with pytest.raises(ValueError, match="required when vector_store='pgvector'"):
        _build_vector_store(settings)
