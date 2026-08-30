"""The composition root binds ports to adapters, and defers clearly."""

from __future__ import annotations

import pytest
from app.adapters.bedrock.embedding import BedrockEmbeddingProvider
from app.core.config import AWSSettings, DatabaseSettings, Environment, Settings, WorkerSettings
from app.core.container import AdapterNotAvailableError, build_container, build_worker_container
from app.ports.llm_provider import EmbeddingProvider, LLMProvider
from app.ports.vector_store import VectorStore


def test_local_defaults_bind_test_doubles(settings: Settings) -> None:
    container = build_container(settings)
    assert isinstance(container.llm, LLMProvider)
    assert isinstance(container.embeddings, EmbeddingProvider)
    assert isinstance(container.vectors, VectorStore)


def test_unimplemented_adapter_names_the_phase_that_adds_it() -> None:
    with pytest.raises(AdapterNotAvailableError, match="Phase 4"):
        build_container(Settings(llm_provider="recorded"))


def test_s3_vectors_is_deferred_to_phase_11() -> None:
    with pytest.raises(AdapterNotAvailableError, match="Phase 11"):
        build_container(Settings(vector_store="s3_vectors"))


def test_deployed_container_binds_bedrock_llm_and_embeddings() -> None:
    settings = Settings(
        llm_provider="bedrock",
        environment=Environment.DEV,
        identity_provider="cognito",
        cognito={"user_pool_id": "us-east-1_mock", "client_id": "mock_client"},
    )
    container = build_container(settings)
    assert isinstance(container.embeddings, BedrockEmbeddingProvider)
    assert isinstance(container.llm, LLMProvider)
    assert container.llm.chat_model_id == settings.bedrock.chat_model


def test_worker_container_binds_all_required_ports() -> None:
    worker_settings = WorkerSettings(
        environment=Environment.DEV,
        vector_store="pgvector",
        llm_provider="bedrock",
        aws=AWSSettings(
            aurora_cluster_arn="arn:aws:rds:us-east-1:123456789012:cluster:cloudguard-dev",
            aurora_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:db-creds",
        ),
        database=DatabaseSettings(name="cloudguard"),
    )
    container = build_worker_container(worker_settings)
    assert isinstance(container.embeddings, BedrockEmbeddingProvider)
    assert isinstance(container.vectors, VectorStore)
    assert container.documents is not None
    assert container.events is not None
