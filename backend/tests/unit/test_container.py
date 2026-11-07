"""The composition root binds ports to adapters, and defers clearly."""

from __future__ import annotations

import pytest
from app.adapters.bedrock.embedding import BedrockEmbeddingProvider
from app.adapters.bedrock.llm import BedrockLLMProvider
from app.core.config import (
    AgentWorkerSettings,
    AWSSettings,
    DatabaseSettings,
    Environment,
    Settings,
    WorkerSettings,
)
from app.core.container import (
    AdapterNotAvailableError,
    build_container,
    build_research_agent_worker_container,
    build_reviewer_agent_worker_container,
    build_risk_agent_worker_container,
    build_worker_container,
)
from app.core.errors import UpstreamError
from app.models.ai import GenerationRequest, Message
from app.ports.llm_provider import EmbeddingProvider, LLMProvider
from app.ports.vector_store import VectorStore


def test_local_defaults_bind_test_doubles(settings: Settings) -> None:
    container = build_container(settings)
    assert isinstance(container.llm, LLMProvider)
    assert isinstance(container.reviewer_llm, LLMProvider)
    assert container.reviewer_llm is not container.llm
    assert isinstance(container.embeddings, EmbeddingProvider)
    assert isinstance(container.vectors, VectorStore)


def test_unimplemented_adapter_names_the_phase_that_adds_it() -> None:
    with pytest.raises(AdapterNotAvailableError, match="Phase 4"):
        build_container(Settings(llm_provider="recorded"))


def test_s3_vectors_requires_bucket_and_index_name() -> None:
    with pytest.raises(
        ValueError,
        match="AWS_S3_VECTOR_INDEX_ARN or both AWS_S3_VECTOR_BUCKET_NAME",
    ):
        build_container(Settings(vector_store="s3_vectors"))


def test_s3_vectors_binds_s3_vector_store_when_configured() -> None:
    from app.adapters.aws.s3_vector_store import S3VectorStore

    # Configured with bucket and index name
    settings = Settings(
        vector_store="s3_vectors",
        aws={
            "s3_vector_bucket_name": "test-vector-bucket",
            "s3_vector_index_name": "test-index",
        },
    )
    container = build_container(settings)
    assert isinstance(container.vectors, S3VectorStore)

    # Configured with index ARN
    arn = (
        "arn:aws:s3vectors:us-east-1:123456789012:"
        "bucket/test-bucket/index/test-index"
    )
    arn_settings = Settings(
        vector_store="s3_vectors",
        aws={
            "s3_vector_index_arn": arn,
        },
    )
    arn_container = build_container(arn_settings)
    assert isinstance(arn_container.vectors, S3VectorStore)


def test_deployed_container_binds_bedrock_llm_and_embeddings() -> None:
    settings = Settings(
        llm_provider="bedrock",
        environment=Environment.DEV,
        identity_provider="cognito",
        rate_limit_table_name="test-rate-limits",
        cognito={"user_pool_id": "us-east-1_mock", "client_id": "mock_client"},
    )
    container = build_container(settings)
    assert isinstance(container.embeddings, BedrockEmbeddingProvider)
    assert isinstance(container.llm, LLMProvider)
    assert isinstance(container.reviewer_llm, LLMProvider)
    assert container.llm.chat_model_id == settings.bedrock.chat_model
    assert container.reviewer_llm.chat_model_id == settings.bedrock.judge_model
    assert container.reviewer_llm is not container.llm


def test_worker_container_binds_all_required_ports() -> None:
    worker_settings = WorkerSettings(
        rate_limit_table_name="test-admission",
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

def test_research_agent_worker_container_binds_retrieval_dependencies() -> None:
    settings = AgentWorkerSettings(
        rate_limit_table_name="test-admission",
        environment=Environment.DEV,
        llm_provider="bedrock",
        vector_store="pgvector",
        aws=AWSSettings(
            aurora_cluster_arn="arn:aws:rds:us-east-1:123456789012:cluster:cloudguard-dev",
            aurora_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:db-creds",
        ),
        database=DatabaseSettings(name="cloudguard"),
    )

    container = build_research_agent_worker_container(settings)

    assert isinstance(container.llm, LLMProvider)
    assert container.llm.chat_model_id == settings.bedrock.chat_model
    assert isinstance(container.embeddings, EmbeddingProvider)
    assert isinstance(container.vectors, VectorStore)


def test_risk_agent_worker_container_binds_only_generator_llm() -> None:
    settings = AgentWorkerSettings(
        rate_limit_table_name="test-admission",
        environment=Environment.DEV,
        llm_provider="bedrock",
    )

    container = build_risk_agent_worker_container(settings)

    assert isinstance(container.llm, LLMProvider)
    assert container.llm.chat_model_id == settings.bedrock.chat_model


def test_reviewer_agent_worker_container_binds_only_reviewer_llm() -> None:
    settings = AgentWorkerSettings(
        rate_limit_table_name="test-admission",
        environment=Environment.DEV,
        llm_provider="bedrock",
    )

    container = build_reviewer_agent_worker_container(settings)

    assert isinstance(container.reviewer_llm, LLMProvider)
    assert container.reviewer_llm.chat_model_id == settings.bedrock.judge_model


def test_container_wires_shared_telemetry_to_all_bedrock_llm_builders() -> None:
    settings = Settings(
        llm_provider="bedrock",
        environment=Environment.LOCAL,
        rate_limit_table_name="test-admission",
    )
    container = build_container(settings)
    assert isinstance(container.llm, BedrockLLMProvider)
    assert isinstance(container.reviewer_llm, BedrockLLMProvider)
    assert container.llm._telemetry is container.telemetry
    assert container.reviewer_llm._telemetry is container.telemetry

    worker_settings = AgentWorkerSettings(
        llm_provider="bedrock",
        environment=Environment.LOCAL,
        rate_limit_table_name="test-admission",
    )
    research_c = build_research_agent_worker_container(worker_settings)
    assert isinstance(research_c.llm, BedrockLLMProvider)
    assert research_c.llm._telemetry is research_c.telemetry

    risk_c = build_risk_agent_worker_container(worker_settings)
    assert isinstance(risk_c.llm, BedrockLLMProvider)
    assert risk_c.llm._telemetry is risk_c.telemetry

    reviewer_c = build_reviewer_agent_worker_container(worker_settings)
    assert isinstance(reviewer_c.reviewer_llm, BedrockLLMProvider)
    assert reviewer_c.reviewer_llm._telemetry is reviewer_c.telemetry


@pytest.mark.asyncio
async def test_container_bedrock_llm_guardrail_intervention_records_usage_once_and_raises() -> None:
    class _MockBedrockClient:
        def converse(self, **kwargs: object) -> dict[str, object]:
            return {
                "usage": {"inputTokens": 150, "outputTokens": 35},
                "stopReason": "guardrail_intervened",
                "guardrailAction": "INTERVENED",
                "metrics": {"latencyMs": 180},
            }

    settings = Settings(
        llm_provider="bedrock",
        environment=Environment.LOCAL,
        rate_limit_table_name="test-admission",
    )
    container = build_container(settings)
    assert isinstance(container.llm, BedrockLLMProvider)
    # Inject test client on container's built LLM
    container.llm._client = _MockBedrockClient()

    req = GenerationRequest(
        messages=[Message(role="user", content="System override")],
    )

    with pytest.raises(
        UpstreamError,
        match=r"The model generation request was blocked by safety controls\.",
    ):
        await container.llm.generate(req)

    # Verify container's shared telemetry emitter recorded available usage exactly once
    emitter = container.telemetry
    assert hasattr(emitter, "metrics")
    input_records = [m for m in emitter.metrics if m.metric_name == "InputTokens"]
    output_records = [m for m in emitter.metrics if m.metric_name == "OutputTokens"]
    interventions = [m for m in emitter.metrics if m.metric_name == "BedrockGuardrailInterventions"]
    latencies = [
        m for m in emitter.metrics
        if m.metric_name == "OperationDurationMs" and m.dimensions.get("Status") == "error"
    ]

    assert len(input_records) == 1
    assert input_records[0].value == 150.0
    assert len(output_records) == 1
    assert output_records[0].value == 35.0
    assert len(interventions) == 1
    assert len(latencies) == 1
    assert len(emitter.costs) == 1
    assert emitter.costs[0].total_tokens == 185
    assert emitter.costs[0].is_measured is True
