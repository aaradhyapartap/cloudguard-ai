"""Unit tests for Phase 10 operational telemetry, pricing, and bounded dimensionality."""

from __future__ import annotations

import io
import json
import re
import sys
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.adapters.aws.telemetry import (
    DEFAULT_CLOUDWATCH_NAMESPACE,
    CloudWatchEMFTelemetryEmitter,
    compute_emf_dimension_sets,
    sanitize_cloudwatch_dimensions,
)
from app.adapters.bedrock.llm import BedrockLLMProvider
from app.adapters.local.rate_limit_store import InMemoryRateLimitStore
from app.adapters.local.telemetry import InMemoryTelemetryEmitter
from app.adapters.local.work_lease_store import InMemoryWorkLeaseStore
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.adapters.mock.llm import MockLLMProvider
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.api.middleware import RequestContextMiddleware, _normalize_http_method
from app.core.errors import RateLimitError, UpstreamError
from app.models.ai import (
    GenerationRequest,
    GenerationResponse,
    TokenUsage,
    VectorMatch,
    VectorRecord,
)
from app.models.principal import Principal, Role
from app.models.rag import RAGRequest
from app.models.research_agent import ResearchAgentRequest
from app.models.retrieval import RetrievalRequest
from app.models.reviewer_agent import ReviewerAgentRequest
from app.models.risk_agent import RiskAgentRequest, RiskEvidenceEstimate
from app.models.telemetry import (
    ALLOWED_METRIC_DIMENSIONS,
    PRICING_CATALOG_VERSION,
    PROHIBITED_METRIC_DIMENSIONS,
    AdmissionCategory,
    CostEstimate,
    MetricUnit,
    OperationalSignal,
    TelemetryMetricRecord,
    sanitize_admission_reason,
    sanitize_bounded_dimension_value,
)
from app.ports.telemetry import TelemetryEmitter
from app.services.rag import RAGService
from app.services.research_agent import ResearchAgent
from app.services.retrieval import RetrievalService
from app.services.reviewer_agent import ReviewerAgent
from app.services.risk_agent import RiskAgent
from app.services.tool_registry import ToolRegistry
from app.services.work_admission import WorkAdmission, WorkKind
from app.telemetry.pricing import (
    estimate_query_cost,
    get_model_pricing_rate,
)
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient


def _make_principal(role: Role = Role.ANALYST) -> Principal:
    org_id = uuid4()
    return Principal(
        user_id=uuid4(),
        organization_id=org_id,
        role=role,
        email="test@cloudguard.ai",
    )


# ---------------------------------------------------------------------------
# 1. Versioned Bedrock Pricing Catalog
# ---------------------------------------------------------------------------


def test_pricing_catalog_versioned_rates() -> None:
    assert PRICING_CATALOG_VERSION == "2026-03-01"

    # Known models return valid rates
    rate_claude = get_model_pricing_rate("anthropic.claude-haiku-4-5-20251001-v1:0")
    assert rate_claude is not None
    assert rate_claude.input_cost_per_thousand == 0.001
    assert rate_claude.output_cost_per_thousand == 0.005

    rate_nova = get_model_pricing_rate("amazon.nova-pro-v1:0")
    assert rate_nova is not None
    assert rate_nova.input_cost_per_thousand == 0.0008
    assert rate_nova.output_cost_per_thousand == 0.0032

    rate_titan = get_model_pricing_rate("amazon.titan-embed-text-v2:0")
    assert rate_titan is not None
    assert rate_titan.input_cost_per_thousand == 0.00002

    # Unknown or unlisted models return None ("Unknown costs remain unmeasured")
    assert get_model_pricing_rate("unknown-model-xyz") is None
    assert get_model_pricing_rate("mock-llm") is None

    # Mismatched pricing version returns None
    assert get_model_pricing_rate("amazon.nova-pro-v1:0", pricing_version="2024-01-01") is None


def test_estimate_query_cost_known_model() -> None:
    # 2000 input tokens ($0.002) + 500 output tokens ($0.0025) = $0.0045
    cost = estimate_query_cost(
        model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        input_tokens=2000,
        output_tokens=500,
    )
    assert cost.is_estimated is True
    assert cost.is_measured is True
    assert cost.total_tokens == 2500
    assert cost.pricing_version == "2026-03-01"
    assert cost.estimated_inference_cost_usd == pytest.approx(0.0045, abs=1e-6)
    assert cost.actual_billed_cost_usd is None


def test_estimate_query_cost_unknown_model_remains_unmeasured() -> None:
    cost = estimate_query_cost(
        model_id="offline-fixture-model",
        input_tokens=1500,
        output_tokens=300,
    )
    assert cost.is_estimated is True
    assert cost.is_measured is False
    assert cost.total_tokens == 1800
    assert cost.estimated_inference_cost_usd is None


def test_estimate_query_cost_with_actual_billed_cost() -> None:
    cost = estimate_query_cost(
        model_id="anthropic.claude-3-haiku-20240307-v1:0",
        input_tokens=1000,
        output_tokens=1000,
        actual_billed_usd=0.0015,
    )
    assert cost.is_measured is True
    assert cost.actual_billed_cost_usd == 0.0015
    assert cost.estimated_inference_cost_usd is not None


# ---------------------------------------------------------------------------
# 2. Metric Record Bounded Dimensions Validation
# ---------------------------------------------------------------------------


def test_metric_record_allows_bounded_dimensions() -> None:
    record = TelemetryMetricRecord(
        metric_name="OperationDurationMs",
        value=42.5,
        unit=MetricUnit.MILLISECONDS,
        dimensions={
            "Environment": "dev",
            "Operation": "retrieval_search",
            "Status": "success",
            "Category": "api_rate_limit",
        },
    )
    assert record.metric_name == "OperationDurationMs"
    assert record.dimensions["Operation"] == "retrieval_search"


@pytest.mark.parametrize(
    "prohibited_key",
    [
        "user_id",
        "document_id",
        "request_id",
        "tenant_id",
        "organization_id",
        "prompt",
        "content",
        "evidence",
        "auth_token",
        "api_key",
        "secret",
    ],
)
def test_metric_record_rejects_prohibited_and_high_cardinality_dimensions(
    prohibited_key: str,
) -> None:
    with pytest.raises(ValueError, match="prohibited as a metric dimension"):
        TelemetryMetricRecord(
            metric_name="OperationDurationMs",
            value=12.0,
            dimensions={prohibited_key: "bad-value"},
        )


# ---------------------------------------------------------------------------
# 3. In-Memory Telemetry Emitter
# ---------------------------------------------------------------------------


def test_in_memory_telemetry_emitter_recording() -> None:
    emitter = InMemoryTelemetryEmitter()

    # Latency
    emitter.record_latency(
        "rag_generate_answer",
        150.0,
        dimensions={"Operation": "rag_generate_answer"},
    )
    assert len(emitter.metrics) == 1
    assert emitter.metrics[0].metric_name == "OperationDurationMs"
    assert emitter.metrics[0].value == 150.0

    # Counter
    emitter.record_counter(
        "RetrievalMatchesCount",
        5.0,
        dimensions={"Operation": "retrieval_search"},
    )
    assert len(emitter.metrics) == 2
    assert emitter.metrics[1].metric_name == "RetrievalMatchesCount"
    assert emitter.metrics[1].value == 5.0

    # Token usage
    cost = emitter.record_token_usage(
        model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        input_tokens=1000,
        output_tokens=200,
    )
    assert len(emitter.costs) == 1
    assert cost.total_tokens == 1200

    # Admission decision
    emitter.record_admission_decision(
        category=AdmissionCategory.API_RATE_LIMIT,
        decision="allowed",
    )
    assert len(emitter.admission_decisions) == 1
    assert emitter.admission_decisions[0]["decision"] == "allowed"

    # Operational signal
    signal = OperationalSignal(
        event_name="agent_workflow_completed",
        stage="workflow",
        status="succeeded",
        duration_ms=450.0,
    )
    emitter.record_operational_signal(signal)
    assert len(emitter.signals) == 1

    # Clear
    emitter.clear()
    assert len(emitter.metrics) == 0
    assert len(emitter.costs) == 0
    assert len(emitter.admission_decisions) == 0
    assert len(emitter.signals) == 0


def test_in_memory_telemetry_fail_safe_suppression() -> None:
    emitter = InMemoryTelemetryEmitter()
    # Passing an invalid dimension that triggers validation inside TelemetryMetricRecord
    # must be suppressed by the emitter and not crash caller execution
    emitter.record_latency(
        "test_op",
        100.0,
        dimensions={"user_id": "malicious-high-cardinality"},
    )
    assert len(emitter.metrics) == 0


# ---------------------------------------------------------------------------
# 4. CloudWatch EMF Telemetry Emitter
# ---------------------------------------------------------------------------


def test_sanitize_cloudwatch_dimensions() -> None:
    raw = {
        "operation": "retrieval_search",
        "Status": "success",
        "user_id": "user-123",
        "document_id": "doc-456",
        "prompt": "secret prompt text",
        "Category": "api_rate_limit",
    }
    clean = sanitize_cloudwatch_dimensions(raw)
    assert "Operation" in clean  # Canonicalized
    assert clean["Operation"] == "retrieval_search"
    assert clean["Status"] == "success"
    assert clean["Category"] == "api_rate_limit"
    assert "user_id" not in clean
    assert "document_id" not in clean
    assert "prompt" not in clean


def test_cloudwatch_emf_telemetry_emitter_stdout() -> None:
    emitter = CloudWatchEMFTelemetryEmitter(
        namespace=DEFAULT_CLOUDWATCH_NAMESPACE,
        environment="test",
    )

    captured_stdout = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured_stdout

    try:
        emitter.record_latency(
            "rag_generate_answer",
            123.45,
            dimensions={"Operation": "rag_generate_answer", "Status": "success"},
            correlation_id="corr-123",
        )
    finally:
        sys.stdout = old_stdout

    output_lines = captured_stdout.getvalue().strip().split("\n")
    assert len(output_lines) == 1

    payload = json.loads(output_lines[0])
    assert "_aws" in payload
    assert payload["_aws"]["CloudWatchMetrics"][0]["Namespace"] == DEFAULT_CLOUDWATCH_NAMESPACE
    assert payload["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Name"] == "OperationDurationMs"
    assert payload["OperationDurationMs"] == 123.45
    assert payload["Environment"] == "test"
    assert payload["Operation"] == "rag_generate_answer"
    assert payload["correlation_id"] == "corr-123"


# ---------------------------------------------------------------------------
# 5. Service Telemetry Integration: RetrievalService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_service_emits_telemetry() -> None:
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    embedding_provider = MockEmbeddingProvider(dimensions=1536)
    vector_store = InMemoryVectorStore()
    retrieval_service = RetrievalService(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        telemetry=emitter,
    )

    req = RetrievalRequest(query="security compliance policy", top_k=3)
    response = await retrieval_service.search(principal=principal, request=req)

    assert response.total == 0
    # Should have emitted latency and matches count
    metric_names = [m.metric_name for m in emitter.metrics]
    assert "OperationDurationMs" in metric_names
    assert "RetrievalMatchesCount" in metric_names

    # Ensure no query text was logged as dimension
    for metric in emitter.metrics:
        for dim_val in metric.dimensions.values():
            assert "security compliance policy" not in dim_val


@pytest.mark.asyncio
async def test_retrieval_service_records_error_counter_on_failure() -> None:
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    class FailingEmbeddingProvider:
        async def embed(self, texts: list[str]) -> None:
            raise UpstreamError("Bedrock connection timeout")

    retrieval_service = RetrievalService(
        embedding_provider=FailingEmbeddingProvider(),  # type: ignore[arg-type]
        vector_store=InMemoryVectorStore(),
        telemetry=emitter,
    )

    with pytest.raises(UpstreamError):
        await retrieval_service.search(
            principal=principal,
            request=RetrievalRequest(query="test failure", top_k=2),
        )

    metric_names = [m.metric_name for m in emitter.metrics]
    assert "RetrievalErrors" in metric_names


# ---------------------------------------------------------------------------
# 6. Service Telemetry Integration: RAGService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_service_emits_latency_and_token_usage() -> None:
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    embedding_provider = MockEmbeddingProvider(dimensions=1536)
    vector_store = InMemoryVectorStore()
    # Seed vector store with one match
    await vector_store.upsert([
        VectorRecord(
            organization_id=str(principal.organization_id),
            document_id=str(uuid4()),
            chunk_id=str(uuid4()),
            content="Access control policy requires MFA for all administrators. [S1]",
            embedding=[0.01] * 1536,
            metadata={"confidentiality_level": "internal"},
        )
    ])

    llm_provider = MockLLMProvider()
    retrieval_service = RetrievalService(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        telemetry=emitter,
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=llm_provider,
        telemetry=emitter,
    )

    req = RAGRequest(query="What is the MFA policy?", top_k=2)
    response = await rag_service.generate_answer(principal=principal, request=req)

    assert response.sources is not None
    # Verify RAG telemetry recorded
    metric_names = [m.metric_name for m in emitter.metrics]
    assert "OperationDurationMs" in metric_names

    # Check token usage recording and pricing estimate
    assert len(emitter.costs) >= 1
    cost = emitter.costs[0]
    assert cost.total_tokens > 0


@pytest.mark.asyncio
async def test_rag_service_zero_evidence_telemetry() -> None:
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    retrieval_service = RetrievalService(
        embedding_provider=MockEmbeddingProvider(dimensions=1536),
        vector_store=InMemoryVectorStore(),
        telemetry=emitter,
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=MockLLMProvider(),
        telemetry=emitter,
    )

    response = await rag_service.generate_answer(
        principal=principal,
        request=RAGRequest(query="Empty indexed documents", top_k=2),
    )
    assert response.retrieval_count == 0

    rag_latency_records = [
        m for m in emitter.metrics
        if m.dimensions.get("Operation") == "rag_generate_answer"
        and m.metric_name == "OperationDurationMs"
    ]
    assert len(rag_latency_records) == 1
    assert rag_latency_records[0].dimensions["Status"] == "no_evidence"


# ---------------------------------------------------------------------------
# 7. Service Telemetry Integration: WorkAdmission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_work_admission_records_allowed_and_denied_decisions() -> None:
    emitter = InMemoryTelemetryEmitter()
    org_id = uuid4()

    admission = WorkAdmission(
        leases=InMemoryWorkLeaseStore(),
        rates=InMemoryRateLimitStore(),
        telemetry=emitter,
    )

    # 1. Successful admission
    async with admission.admit(organization_id=org_id, kind=WorkKind.MODEL):
        pass

    assert len(emitter.admission_decisions) >= 1
    assert emitter.admission_decisions[-1]["decision"] == "allowed"

    # 2. Slot exhaustion denial: simulate all slots taken
    exhausted_leases = InMemoryWorkLeaseStore()
    # Acquire all slots
    await exhausted_leases.try_acquire(
        key=f"slot:v1:ingestion:{uuid4().hex}:0",
        owner="other",
        now=0,
        expires_at=999999,
    )

    class AlwaysBusyLeaseStore(InMemoryWorkLeaseStore):
        async def try_acquire(self, *, key: str, owner: str, now: int, expires_at: int) -> bool:
            return False

    busy_admission = WorkAdmission(
        leases=AlwaysBusyLeaseStore(),
        rates=InMemoryRateLimitStore(),
        telemetry=emitter,
    )

    with pytest.raises(RateLimitError):
        async with busy_admission.admit(organization_id=org_id, kind=WorkKind.INGESTION):
            pass

    assert emitter.admission_decisions[-1]["decision"] == "denied"
    assert emitter.admission_decisions[-1]["reason"] == "slot_exhausted"


# ---------------------------------------------------------------------------
# 8. Privacy Boundary: Invariant Verification
# ---------------------------------------------------------------------------


def test_telemetry_privacy_invariant() -> None:
    """Verifies that metric dimensions never contain sensitive or high-cardinality keys."""
    for prohibited in PROHIBITED_METRIC_DIMENSIONS:
        assert prohibited not in ALLOWED_METRIC_DIMENSIONS


# ---------------------------------------------------------------------------
# 9. Raising Telemetry Emitter Isolation Tests
# ---------------------------------------------------------------------------


class RaisingTelemetryEmitter(TelemetryEmitter):
    """Telemetry double that raises on every call to verify application isolation."""

    def record_counter(
        self,
        metric_name: str,
        value: float = 1.0,
        *,
        dimensions: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        raise RuntimeError("Telemetry failure: record_counter failed")

    def record_latency(
        self,
        operation: str,
        duration_ms: float,
        *,
        dimensions: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        raise RuntimeError("Telemetry failure: record_latency failed")

    def record_token_usage(
        self,
        *,
        model_id: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        operation: str = "inference",
        correlation_id: str | None = None,
        actual_billed_usd: float | None = None,
    ) -> CostEstimate:
        raise RuntimeError("Telemetry failure: record_token_usage failed")

    def record_admission_decision(
        self,
        *,
        category: AdmissionCategory,
        decision: str,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        raise RuntimeError("Telemetry failure: record_admission_decision failed")

    def record_operational_signal(
        self,
        signal: OperationalSignal,
    ) -> None:
        raise RuntimeError("Telemetry failure: record_operational_signal failed")

    def record_query_cost(
        self,
        *,
        total_cost_usd: float | None,
        is_measured: bool,
        is_partial: bool = False,
        operation: str = "rag_generate_answer",
        correlation_id: str | None = None,
    ) -> None:
        raise RuntimeError("Telemetry failure: record_query_cost failed")


@pytest.mark.asyncio
async def test_raising_telemetry_emitter_does_not_break_retrieval() -> None:
    raising_emitter = RaisingTelemetryEmitter()
    principal = _make_principal()
    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider(dimensions=1536)

    # Seed vector
    vector_res = await embedding_provider.embed(["test content"])
    await vector_store.upsert([
        VectorRecord(
            chunk_id=str(uuid4()),
            document_id=str(uuid4()),
            organization_id=str(principal.organization_id),
            embedding=vector_res.vectors[0],
            content="test content",
            metadata={"confidentiality_level": "internal"},
        )
    ])

    service = RetrievalService(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        telemetry=raising_emitter,
    )
    # Must succeed cleanly despite raising emitter
    res = await service.search(principal=principal, request=RetrievalRequest(query="test"))
    assert res.total == 1


@pytest.mark.asyncio
async def test_raising_telemetry_emitter_does_not_mask_upstream_error() -> None:
    raising_emitter = RaisingTelemetryEmitter()
    principal = _make_principal()

    class BrokenEmbeddingProvider(MockEmbeddingProvider):
        async def embed(self, texts: list[str]) -> Any:
            raise RuntimeError("Underlying provider crashed")

    service = RetrievalService(
        embedding_provider=BrokenEmbeddingProvider(dimensions=1536),
        vector_store=InMemoryVectorStore(),
        telemetry=raising_emitter,
    )
    # Original upstream error must propagate, not the telemetry error
    with pytest.raises(UpstreamError) as exc_info:
        await service.search(principal=principal, request=RetrievalRequest(query="test"))
    assert "query could not be embedded" in str(exc_info.value)


@pytest.mark.asyncio
async def test_raising_telemetry_emitter_does_not_break_work_admission_or_lease_release() -> None:
    raising_emitter = RaisingTelemetryEmitter()
    leases = InMemoryWorkLeaseStore()
    rates = InMemoryRateLimitStore()
    admission = WorkAdmission(leases=leases, rates=rates, telemetry=raising_emitter)
    org_id = uuid4()

    # Successful admit must succeed and release lease in finally
    async with admission.admit(organization_id=org_id, kind=WorkKind.MODEL):
        pass

    # Denied admit must raise RateLimitError, not the telemetry RuntimeError
    class BusyLeaseStore(InMemoryWorkLeaseStore):
        async def try_acquire(self, *, key: str, owner: str, now: int, expires_at: int) -> bool:
            return False

    busy_admission = WorkAdmission(leases=BusyLeaseStore(), rates=rates, telemetry=raising_emitter)
    with pytest.raises(RateLimitError):
        async with busy_admission.admit(organization_id=org_id, kind=WorkKind.INGESTION):
            pass


# ---------------------------------------------------------------------------
# 10. Canary and High-Cardinality Controls
# ---------------------------------------------------------------------------


def test_canary_and_privacy_controls() -> None:
    # 1. Canary patterns and unknown free-text attributes in safe_attributes are stripped
    sig = OperationalSignal(
        event_name="test_event",
        stage="workflow",
        status="success",
        safe_attributes={
            "api_key": "sk_live_1234567890abcdef12345678",
            "header": "Bearer secret_token_xyz",
            "db": "password=supersecret",
            "source_label": "normal_val",  # Unknown attribute key: dropped
            "failed_step": "research",     # Known closed-set attribute key & value: kept
            "retrieval_count": 5,          # Known numeric attribute key & value: kept
        },
    )
    assert "api_key" not in sig.safe_attributes
    assert "header" not in sig.safe_attributes
    assert "db" not in sig.safe_attributes
    assert "source_label" not in sig.safe_attributes  # Free text key dropped
    assert sig.safe_attributes.get("failed_step") == "research"
    assert sig.safe_attributes.get("retrieval_count") == 5

    # 2. Canary in dimension value gets sanitized
    assert sanitize_bounded_dimension_value("Operation", "sk_live_1234") == "other"
    assert sanitize_bounded_dimension_value("ModelId", "my_secret_token_123") == "other"

    # 3. Prohibited dimension names raise ValueError in TelemetryMetricRecord
    with pytest.raises(ValueError, match="is prohibited as a metric dimension"):
        TelemetryMetricRecord(
            metric_name="TestMetric",
            value=1.0,
            dimensions={"user_id": "1234"},
        )


def test_high_cardinality_dimension_values_are_mapped_to_safe_categories() -> None:
    # Random UUID as operation
    assert sanitize_bounded_dimension_value("Operation", str(uuid4())) == "other"
    # Arbitrary HTTP method
    assert _normalize_http_method("PROPFIND") == "OTHER"
    assert _normalize_http_method("ATTACK") == "OTHER"
    assert _normalize_http_method("") == "OTHER"
    # Status
    assert sanitize_bounded_dimension_value("Status", "SOMETHING_RANDOM_999") == "unknown"
    # Category
    assert sanitize_bounded_dimension_value("Category", "invented_category") == "other"
    # AdmissionReason
    assert sanitize_admission_reason("some random user error message") == "other"
    # ModelId
    assert (
        sanitize_bounded_dimension_value("ModelId", "random-huggingface-model-777")
        == "unlisted_model"
    )


def test_in_memory_telemetry_emitter_bounded_retention() -> None:
    emitter = InMemoryTelemetryEmitter(max_records=5)
    for i in range(12):
        emitter.record_counter("TestCounter", float(i), dimensions={"Operation": "http_request"})
        emitter.record_token_usage(
            model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
            input_tokens=100,
            output_tokens=50,
            operation="rag_generate_answer",
        )
        emitter.record_operational_signal(
            OperationalSignal(
                event_name="test_event",
                stage="workflow",
                status="success",
            )
        )
        emitter.record_admission_decision(
            category=AdmissionCategory.API_RATE_LIMIT,
            decision="allowed",
        )

    assert len(emitter.metrics) == 5
    # Oldest records were evicted, maxlen strictly bounded
    assert emitter.metrics[-1].metric_name == "AdmissionAllowed"
    assert emitter.metrics[-1].value == 1.0
    assert len(emitter.costs) == 5
    assert len(emitter.signals) == 5
    assert len(emitter.admission_decisions) == 5


# ---------------------------------------------------------------------------
# 11. Agent Telemetry Wiring Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_agent_records_tokens_even_when_validation_fails() -> None:
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    class InvalidJsonLLMProvider(MockLLMProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            from datetime import UTC, datetime
            return GenerationResponse(
                content="Not valid JSON at all",
                model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
                usage=TokenUsage(input_tokens=150, output_tokens=30),
                stop_reason="end_turn",
                latency_ms=10,
                generated_at=datetime.now(UTC),
            )

    retrieval_service = RetrievalService(
        embedding_provider=MockEmbeddingProvider(dimensions=1536),
        vector_store=InMemoryVectorStore(),
        telemetry=emitter,
    )
    tool_registry = ToolRegistry(retrieval_service=retrieval_service)
    agent = ResearchAgent(
        llm_provider=InvalidJsonLLMProvider(),
        tool_registry=tool_registry,
        telemetry=emitter,
    )

    with pytest.raises(UpstreamError) as exc_info:
        await agent.research(
            principal=principal,
            request=ResearchAgentRequest(query="test query"),
        )
    assert "invalid tool intent" in str(exc_info.value)

    # Verify that model latency and token usage WERE recorded before validation rejected output
    model_latencies = [
        m for m in emitter.metrics
        if m.dimensions.get("Operation") == "research_agent"
        and m.metric_name == "OperationDurationMs"
    ]
    assert len(model_latencies) == 1
    assert len(emitter.costs) == 1
    assert emitter.costs[0].total_tokens == 180
    assert emitter.costs[0].estimated_inference_cost_usd is not None


@pytest.mark.asyncio
async def test_risk_and_reviewer_agents_emit_model_telemetry() -> None:
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    # Risk Agent
    risk_agent = RiskAgent(
        llm_provider=MockLLMProvider(),
        telemetry=emitter,
    )
    match = VectorMatch(
        chunk_id=str(uuid4()),
        document_id=str(uuid4()),
        score=0.9,
        content="Evidence content for risk assessment",
        metadata={"confidentiality_level": "internal"},
    )
    await risk_agent.assess(
        principal=principal,
        request=RiskAgentRequest(question="Assess risk", evidence=[match]),
    )
    risk_metrics = [
        m for m in emitter.metrics
        if m.dimensions.get("Operation") == "risk_agent"
        and m.metric_name == "OperationDurationMs"
    ]
    assert len(risk_metrics) == 1

    # Reviewer Agent
    reviewer_agent = ReviewerAgent(
        llm_provider=MockLLMProvider(),
        telemetry=emitter,
    )
    estimate = RiskEvidenceEstimate(
        chunk_id=match.chunk_id,
        likelihood=0.5,
        impact=0.5,
        rationale="Advisory estimate",
    )
    await reviewer_agent.review(
        principal=principal,
        request=ReviewerAgentRequest(
            question="Review risk",
            evidence=[match],
            risk_estimates=[estimate],
        ),
    )
    reviewer_metrics = [
        m for m in emitter.metrics
        if m.dimensions.get("Operation") == "reviewer_agent"
        and m.metric_name == "OperationDurationMs"
    ]
    assert len(reviewer_metrics) == 1


# ---------------------------------------------------------------------------
# 12. HTTP Middleware Telemetry Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_counts_handled_5xx_responses_and_normalizes_methods() -> None:
    emitter = InMemoryTelemetryEmitter()
    app = FastAPI()

    class FakeContainer:
        telemetry = emitter

    app.state.container = FakeContainer()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/test-503")
    async def endpoint_503() -> Response:
        return Response(content="Service Unavailable", status_code=503)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test-503")
        assert response.status_code == 503

    metric_names = [m.metric_name for m in emitter.metrics]
    assert "HttpRequestCount" in metric_names
    assert "OperationDurationMs" in metric_names
    assert "HttpRequestErrors" in metric_names

    error_record = next(m for m in emitter.metrics if m.metric_name == "HttpRequestErrors")
    assert error_record.dimensions["Status"] == "503"
    assert error_record.dimensions["Operation"] == "GET"


# ---------------------------------------------------------------------------
# 13. Query Boundary Accounting vs. Model Call Accounting
# ---------------------------------------------------------------------------


class _ConfigurableEmbeddingProvider(MockEmbeddingProvider):
    def __init__(self, model_id: str, input_tokens: int = 500) -> None:
        super().__init__(dimensions=1536)
        self._model_id = model_id
        self._input_tokens = input_tokens

    @property
    def embedding_model_id(self) -> str:
        return self._model_id

    async def embed(self, texts: list[str]):
        res = await super().embed(texts)
        return res.model_copy(
            update={"model_id": self._model_id, "input_tokens": self._input_tokens}
        )


class _ConfigurableLLMProvider(MockLLMProvider):
    def __init__(
        self,
        model_id: str,
        input_tokens: int | None = 1000,
        output_tokens: int | None = 200,
        canned_response: str = "Test response",
        custom_usage: TokenUsage | object | None = ...,
    ) -> None:
        super().__init__()
        self._model_id = model_id
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._canned_response = canned_response
        self._custom_usage = custom_usage

    @property
    def chat_model_id(self) -> str:
        return self._model_id

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        if self._custom_usage is not ...:
            usage = self._custom_usage if isinstance(self._custom_usage, TokenUsage) else None
        elif self._input_tokens is not None or self._output_tokens is not None:
            usage = TokenUsage(
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
            )
        else:
            usage = None
        return GenerationResponse(
            content=self._canned_response,
            model_id=self._model_id,
            usage=usage,
            stop_reason="end_turn",
            latency_ms=45,
            generated_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_rag_query_with_embedding_and_generation_counts_once_and_sums_cost() -> None:
    """A RAG query with 1 embedding call + 1 generation call counts once at the query boundary.

    - CostMeasuredQueries is emitted exactly once with value 1.0.
    - EstimatedInferenceCostUSD at query boundary is the sum of component model costs.
    - Per-call metrics (CostMeasuredModelCalls, ModelCallEstimatedCostUSD) are emitted per call.
    """
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    # Embedding provider with recognized Bedrock model (amazon.titan-embed-text-v2:0)
    # Rate: $0.00002 per 1000 input tokens. For 500 tokens -> $0.000010
    embedding_provider = _ConfigurableEmbeddingProvider(
        model_id="amazon.titan-embed-text-v2:0",
        input_tokens=500,
    )
    vector_store = InMemoryVectorStore()
    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    await vector_store.upsert([
        VectorRecord(
            organization_id=str(principal.organization_id),
            document_id=doc_id,
            chunk_id=chunk_id,
            content="Standard encryption at rest requires AES-256 keys. [S1]",
            embedding=[0.01] * 1536,
            metadata={"confidentiality_level": "internal"},
        )
    ])

    # LLM provider with recognized Bedrock model (anthropic.claude-haiku-4-5-20251001-v1:0)
    # Rate: input $0.001/1k, output $0.005/1k.
    # For 1000 input tokens, 200 output tokens:
    # input = 1000/1000 * 0.001 = 0.001
    # output = 200/1000 * 0.005 = 0.001 -> total generation = 0.002
    llm_provider = _ConfigurableLLMProvider(
        model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        input_tokens=1000,
        output_tokens=200,
        canned_response="Encryption at rest requires AES-256 keys. [S1]",
    )

    retrieval_service = RetrievalService(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        telemetry=emitter,
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=llm_provider,
        telemetry=emitter,
    )

    req = RAGRequest(query="What encryption standard is required?", top_k=2)
    response = await rag_service.generate_answer(principal=principal, request=req)
    assert response.sources is not None
    assert len(response.sources) == 1

    # 1. Query boundary metrics: CostMeasuredQueries must be emitted EXACTLY ONCE
    measured_queries = [
        m for m in emitter.metrics if m.metric_name == "CostMeasuredQueries"
    ]
    assert len(measured_queries) == 1
    assert measured_queries[0].value == 1.0

    # 2. Query boundary total cost: sum of embedding ($0.000010) + generation ($0.002) = $0.002010
    query_cost_metrics = [
        m for m in emitter.metrics if m.metric_name == "EstimatedInferenceCostUSD"
    ]
    assert len(query_cost_metrics) == 1
    expected_sum = 0.000010 + 0.002
    assert abs(query_cost_metrics[0].value - expected_sum) < 1e-7

    # 3. Model call metrics: CostMeasuredModelCalls must be emitted TWICE (once per call)
    measured_calls = [
        m for m in emitter.metrics if m.metric_name == "CostMeasuredModelCalls"
    ]
    assert len(measured_calls) == 2

    # 4. Model call cost records: 2 separate call-level cost records
    call_costs = [
        m for m in emitter.metrics if m.metric_name == "ModelCallEstimatedCostUSD"
    ]
    assert len(call_costs) == 2
    assert any(abs(m.value - 0.000010) < 1e-7 for m in call_costs)
    assert any(abs(m.value - 0.002) < 1e-7 for m in call_costs)

    # 5. CostUnmeasuredQueries must NOT be emitted
    assert not any(m.metric_name == "CostUnmeasuredQueries" for m in emitter.metrics)


@pytest.mark.asyncio
async def test_rag_query_with_mixed_measured_and_unmeasured_costs_is_partial() -> None:
    """A RAG query with mixed (known + unknown) component costs is marked partial/unmeasured."""
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    # Embedding provider with fixture/unmeasured model (eval:feature-hash-1024-v1)
    embedding_provider = _ConfigurableEmbeddingProvider(
        model_id="eval:feature-hash-1024-v1",
        input_tokens=100,
    )
    vector_store = InMemoryVectorStore()
    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    await vector_store.upsert([
        VectorRecord(
            organization_id=str(principal.organization_id),
            document_id=doc_id,
            chunk_id=chunk_id,
            content="MFA required. [S1]",
            embedding=[0.01] * 1536,
            metadata={"confidentiality_level": "internal"},
        )
    ])

    # LLM provider with recognized model
    llm_provider = _ConfigurableLLMProvider(
        model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        input_tokens=500,
        output_tokens=100,
        canned_response="MFA required. [S1]",
    )

    retrieval_service = RetrievalService(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        telemetry=emitter,
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=llm_provider,
        telemetry=emitter,
    )

    req = RAGRequest(query="MFA policy?", top_k=2)
    await rag_service.generate_answer(principal=principal, request=req)

    # Mixed query must be marked partial/unmeasured, NOT presented as a complete query cost
    assert not any(m.metric_name == "CostMeasuredQueries" for m in emitter.metrics)
    assert not any(m.metric_name == "EstimatedInferenceCostUSD" for m in emitter.metrics)

    unmeasured_queries = [
        m for m in emitter.metrics if m.metric_name == "CostUnmeasuredQueries"
    ]
    assert len(unmeasured_queries) == 1
    assert unmeasured_queries[0].value == 1.0

    partial_queries = [
        m for m in emitter.metrics if m.metric_name == "CostPartialQueries"
    ]
    assert len(partial_queries) == 1
    assert partial_queries[0].value == 1.0

    # Per-call metrics reflect individual components
    assert any(m.metric_name == "CostUnmeasuredModelCalls" for m in emitter.metrics)
    assert any(m.metric_name == "CostMeasuredModelCalls" for m in emitter.metrics)


# ---------------------------------------------------------------------------
# 14. EMF Dimension Sets Deduplication
# ---------------------------------------------------------------------------


def test_compute_emf_dimension_sets_deduplicates_by_unordered_identity_admission() -> None:
    """EMF dimension sets for admission decisions must contain no duplicate combinations."""
    clean_dims = {
        "Environment": "dev",
        "Category": "api_rate_limit",
        "AdmissionDecision": "allowed",
    }
    dim_sets = compute_emf_dimension_sets(clean_dims)

    # Convert each set to frozenset to test unordered identity
    frozensets = [frozenset(s) for s in dim_sets]
    assert len(frozensets) == len(set(frozensets)), (
        f"Duplicate dimension combinations detected in {dim_sets}"
    )

    # Specifically verify the admission decision set appears exactly once
    admission_set = frozenset({"Environment", "Category", "AdmissionDecision"})
    assert frozensets.count(admission_set) == 1
    assert frozensets.count(frozenset({"Environment"})) == 1


def test_compute_emf_dimension_sets_deduplicates_by_unordered_identity_op_model() -> None:
    """EMF dimension sets for operation+model+status must contain no duplicate combinations."""
    clean_dims = {
        "Environment": "prod",
        "Operation": "rag_generate_answer",
        "ModelId": "anthropic.claude-haiku-4-5-20251001-v1:0",
        "Status": "success",
    }
    dim_sets = compute_emf_dimension_sets(clean_dims)

    frozensets = [frozenset(s) for s in dim_sets]
    assert len(frozensets) == len(set(frozensets)), (
        f"Duplicate dimension combinations detected in {dim_sets}"
    )

    # Verify expected subsets appear exactly once
    assert frozensets.count(frozenset({"Environment"})) == 1
    assert frozensets.count(frozenset({"Environment", "Operation"})) == 1
    assert frozensets.count(frozenset({"Environment", "Operation", "Status"})) == 1
    assert frozensets.count(frozenset({"Environment", "ModelId", "Operation"})) == 1
    assert frozensets.count(frozenset({"Environment", "ModelId", "Operation", "Status"})) == 1


# ---------------------------------------------------------------------------
# 15. Operational Safe Attributes Allowlists & Canary Ordering
# ---------------------------------------------------------------------------


def test_safe_attributes_allowlist_and_canary_ordering_positive_negative_controls() -> None:
    """Validate positive and negative controls for safe_attributes and canary ordering."""
    # Positive Control 1: 'document_processing' is preserved as legitimate closed-set operation
    assert (
        sanitize_bounded_dimension_value("Operation", "document_processing")
        == "document_processing"
    )

    # Positive Control 2: 'token_verification_failed' is preserved as legitimate admission reason
    assert (
        sanitize_admission_reason("token_verification_failed")
        == "token_verification_failed"
    )

    # Positive Control 3: Legitimate safe attributes in closed set are preserved
    sig_valid = OperationalSignal(
        event_name="workflow_step",
        stage="workflow",
        status="success",
        safe_attributes={
            "failed_step": "research",
            "operation": "document_processing",
            "component": "document_processing",
            "status": "success",
            "execution_mode": "local",
            "retrieval_count": 3,
            "is_partial": True,
            "error_type": "UpstreamError",
        },
    )
    assert sig_valid.safe_attributes["operation"] == "document_processing"
    assert sig_valid.safe_attributes["component"] == "document_processing"
    assert sig_valid.safe_attributes["failed_step"] == "research"
    assert sig_valid.safe_attributes["retrieval_count"] == 3
    assert sig_valid.safe_attributes["is_partial"] is True
    assert sig_valid.safe_attributes["error_type"] == "UpstreamError"

    # Negative Control 1: Unknown free-text attributes without credential keywords are DROPPED
    sig_unknown = OperationalSignal(
        event_name="workflow_step",
        stage="workflow",
        status="success",
        safe_attributes={
            "free_text_comment": "harmless looking string",
            "user_nickname": "analyst_bob",
            "custom_source": "internal_wiki",
        },
    )
    assert "free_text_comment" not in sig_unknown.safe_attributes
    assert "user_nickname" not in sig_unknown.safe_attributes
    assert "custom_source" not in sig_unknown.safe_attributes
    assert sig_unknown.safe_attributes == {}

    # Negative Control 2: Sensitive canaries in keys or values are stripped/mapped
    sig_canary = OperationalSignal(
        event_name="workflow_step",
        stage="workflow",
        status="success",
        safe_attributes={
            "api_key": "sk_live_12345",
            "failed_step": "sk_live_secret",
            "operation": "bearer_token_op",
        },
    )
    assert "api_key" not in sig_canary.safe_attributes
    assert "failed_step" not in sig_canary.safe_attributes
    assert "operation" not in sig_canary.safe_attributes
    assert sig_canary.safe_attributes == {}


# ---------------------------------------------------------------------------
# 16. Bedrock Adapter Usage Capture Before Guardrail Intervention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bedrock_adapter_captures_usage_at_boundary_before_guardrail_intervention() -> None:
    """BedrockLLMProvider extracts token usage and metrics before raising on
    guardrail intervention.
    """
    emitter = InMemoryTelemetryEmitter()

    # Create mock Bedrock client returning a controlled Converse response with usage & guardrail
    mock_client = MagicMock()
    mock_client.converse.return_value = {
        "stopReason": "guardrail_intervened",
        "usage": {
            "inputTokens": 45,
            "outputTokens": 0,
            "totalTokens": 45,
        },
        "metrics": {
            "latencyMs": 125,
        },
    }

    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=mock_client,
        telemetry=emitter,
    )

    request = GenerationRequest(
        messages=[{"role": "user", "content": "Ignored prompt"}],
        max_tokens=100,
    )

    # Must raise exact safety rejection
    with pytest.raises(
        UpstreamError,
        match=re.escape("The model generation request was blocked by safety controls."),
    ):
        await provider.generate(request)

    # Verify adapter extracted available token usage before guardrail rejection
    input_tokens_metrics = [
        m for m in emitter.metrics if m.metric_name == "InputTokens"
    ]
    assert len(input_tokens_metrics) == 1
    assert input_tokens_metrics[0].value == 45.0
    assert input_tokens_metrics[0].dimensions["Operation"] == "bedrock_converse"

    # Verify cost accounting for recognized model
    measured_calls = [
        m for m in emitter.metrics if m.metric_name == "CostMeasuredModelCalls"
    ]
    assert len(measured_calls) == 1
    assert measured_calls[0].value == 1.0

    # Verify bounded latency recorded
    latency_metrics = [
        m for m in emitter.metrics
        if m.metric_name == "OperationDurationMs"
        and m.dimensions.get("Operation") == "bedrock_converse"
    ]
    assert len(latency_metrics) == 1
    assert latency_metrics[0].value == 125.0
    assert latency_metrics[0].dimensions["Status"] == "error"

    # Verify bounded error signal emitted
    guardrail_counter = [
        m for m in emitter.metrics if m.metric_name == "BedrockGuardrailInterventions"
    ]
    assert len(guardrail_counter) == 1
    assert guardrail_counter[0].value == 1.0
    assert guardrail_counter[0].dimensions["Operation"] == "bedrock_converse"
    assert guardrail_counter[0].dimensions["Status"] == "error"


@pytest.mark.asyncio
async def test_bedrock_adapter_guardrail_intervention_unknown_usage_stays_unknown() -> None:
    """When Bedrock Converse response lacks usage, unknown usage stays unknown."""
    emitter = InMemoryTelemetryEmitter()

    mock_client = MagicMock()
    mock_client.converse.return_value = {
        "stopReason": "guardrail_intervened",
        # No usage dictionary in response
        "metrics": {
            "latencyMs": 95,
        },
    }

    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=mock_client,
        telemetry=emitter,
    )

    request = GenerationRequest(
        messages=[{"role": "user", "content": "Ignored prompt"}],
        max_tokens=100,
    )

    with pytest.raises(
        UpstreamError,
        match=re.escape("The model generation request was blocked by safety controls."),
    ):
        await provider.generate(request)

    # Unknown usage stays unknown: no token metrics emitted
    assert not any(m.metric_name == "InputTokens" for m in emitter.metrics)
    assert not any(m.metric_name == "OutputTokens" for m in emitter.metrics)

    # Latency and guardrail counter are still recorded
    latency_metrics = [
        m for m in emitter.metrics
        if m.metric_name == "OperationDurationMs"
        and m.dimensions.get("Operation") == "bedrock_converse"
    ]
    assert len(latency_metrics) == 1
    assert latency_metrics[0].value == 95.0

    assert any(m.metric_name == "BedrockGuardrailInterventions" for m in emitter.metrics)


# ---------------------------------------------------------------------------
# 18. Unknown Usage Provenance & Container Telemetry Regressions
# ---------------------------------------------------------------------------


def test_estimate_query_cost_usage_absent_or_incomplete_remains_unmeasured() -> None:
    model_id = "anthropic.claude-haiku-4-5-20251001-v1:0"

    # Both absent
    cost_none = estimate_query_cost(model_id=model_id, input_tokens=None, output_tokens=None)
    assert cost_none.input_tokens is None
    assert cost_none.output_tokens is None
    assert cost_none.total_tokens is None
    assert cost_none.estimated_inference_cost_usd is None
    assert cost_none.is_measured is False

    # Input present, output absent
    cost_in_only = estimate_query_cost(model_id=model_id, input_tokens=100, output_tokens=None)
    assert cost_in_only.input_tokens == 100
    assert cost_in_only.output_tokens is None
    assert cost_in_only.total_tokens is None
    assert cost_in_only.estimated_inference_cost_usd is None
    assert cost_in_only.is_measured is False

    # Output present, input absent
    cost_out_only = estimate_query_cost(model_id=model_id, input_tokens=None, output_tokens=50)
    assert cost_out_only.input_tokens is None
    assert cost_out_only.output_tokens == 50
    assert cost_out_only.total_tokens is None
    assert cost_out_only.estimated_inference_cost_usd is None
    assert cost_out_only.is_measured is False

    # Explicitly reported zero counts (must remain distinguishable from absent usage)
    cost_zero = estimate_query_cost(model_id=model_id, input_tokens=0, output_tokens=0)
    assert cost_zero.input_tokens == 0
    assert cost_zero.output_tokens == 0
    assert cost_zero.total_tokens == 0
    assert cost_zero.estimated_inference_cost_usd == 0.0
    assert cost_zero.is_measured is True

    # Complete reported counts
    cost_complete = estimate_query_cost(model_id=model_id, input_tokens=100, output_tokens=50)
    assert cost_complete.input_tokens == 100
    assert cost_complete.output_tokens == 50
    assert cost_complete.total_tokens == 150
    assert cost_complete.estimated_inference_cost_usd == 0.00035
    assert cost_complete.is_measured is True


@pytest.mark.asyncio
async def test_container_level_guardrail_intervention_records_usage_once() -> None:
    from app.core.config import Environment, Settings
    from app.core.container import build_container

    mock_client = MagicMock()
    mock_client.converse.return_value = {
        "usage": {"inputTokens": 200, "outputTokens": 40},
        "stopReason": "guardrail_intervened",
        "guardrailAction": "INTERVENED",
        "metrics": {"latencyMs": 150},
    }

    settings = Settings(
        llm_provider="bedrock",
        environment=Environment.LOCAL,
        rate_limit_table_name="test-admission",
    )
    container = build_container(settings)
    assert isinstance(container.llm, BedrockLLMProvider)
    container.llm._client = mock_client

    request = GenerationRequest(
        messages=[{"role": "user", "content": "Adversarial input"}],
    )

    with pytest.raises(
        UpstreamError,
        match=re.escape("The model generation request was blocked by safety controls."),
    ):
        await container.llm.generate(request)

    emitter = container.telemetry
    assert hasattr(emitter, "metrics")
    input_records = [m for m in emitter.metrics if m.metric_name == "InputTokens"]
    output_records = [m for m in emitter.metrics if m.metric_name == "OutputTokens"]
    guardrail_records = [
        m for m in emitter.metrics if m.metric_name == "BedrockGuardrailInterventions"
    ]

    assert len(input_records) == 1
    assert input_records[0].value == 200.0
    assert len(output_records) == 1
    assert output_records[0].value == 40.0
    assert len(guardrail_records) == 1
    assert len(emitter.costs) == 1
    assert emitter.costs[0].total_tokens == 240
    assert emitter.costs[0].is_measured is True


@pytest.mark.asyncio
async def test_rag_service_call_and_query_coverage_usage_absent() -> None:
    """When generation usage is absent, model call is unmeasured and query cost is partial."""
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    embedding_provider = _ConfigurableEmbeddingProvider(
        model_id="amazon.titan-embed-text-v2:0",
        input_tokens=100,
    )
    vector_store = InMemoryVectorStore()
    retrieval_service = RetrievalService(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        telemetry=emitter,
    )
    doc_id = str(uuid4())
    await vector_store.upsert(
        [
            VectorRecord(
                chunk_id=str(uuid4()),
                document_id=doc_id,
                organization_id=str(principal.organization_id),
                content="Known evidence.",
                embedding=[0.1] * 1536,
                metadata={"confidentiality_level": "internal"},
            )
        ]
    )

    llm_provider = _ConfigurableLLMProvider(
        model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        canned_response="Answer with no usage metadata.",
        custom_usage=None,
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=llm_provider,
        telemetry=emitter,
    )

    response = await rag_service.generate_answer(
        principal=principal,
        request=RAGRequest(query="Question?", top_k=1),
    )
    assert response.answer == "Answer with no usage metadata."
    assert response.usage is None

    # Call coverage: 1 unmeasured generation model call, 1 measured embedding call
    assert (
        len([
            m
            for m in emitter.metrics
            if m.metric_name == "CostUnmeasuredModelCalls"
            and m.dimensions.get("Operation") == "rag_generate_answer"
        ])
        == 1
    )
    assert (
        len([
            m
            for m in emitter.metrics
            if m.metric_name == "CostMeasuredModelCalls"
            and m.dimensions.get("Operation") == "rag_generate_answer"
        ])
        == 0
    )
    assert (
        len([
            m
            for m in emitter.metrics
            if m.metric_name == "CostMeasuredModelCalls"
            and m.dimensions.get("Operation") == "retrieval_search"
        ])
        == 1
    )

    # Query coverage: 1 unmeasured/partial query, 0 measured queries
    assert len([m for m in emitter.metrics if m.metric_name == "CostUnmeasuredQueries"]) == 1
    assert len([m for m in emitter.metrics if m.metric_name == "CostPartialQueries"]) == 1
    assert len([m for m in emitter.metrics if m.metric_name == "CostMeasuredQueries"]) == 0
    assert not any(m.metric_name == "EstimatedInferenceCostUSD" for m in emitter.metrics)


@pytest.mark.asyncio
async def test_rag_service_call_and_query_coverage_one_token_count_absent() -> None:
    """When generation has one token-count field absent, model call and query remain unmeasured."""
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    embedding_provider = _ConfigurableEmbeddingProvider(
        model_id="amazon.titan-embed-text-v2:0",
        input_tokens=100,
    )
    vector_store = InMemoryVectorStore()
    retrieval_service = RetrievalService(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        telemetry=emitter,
    )
    doc_id = str(uuid4())
    await vector_store.upsert(
        [
            VectorRecord(
                chunk_id=str(uuid4()),
                document_id=doc_id,
                organization_id=str(principal.organization_id),
                content="Known evidence.",
                embedding=[0.1] * 1536,
                metadata={"confidentiality_level": "internal"},
            )
        ]
    )

    partial_usage = TokenUsage(input_tokens=150, output_tokens=None)
    llm_provider = _ConfigurableLLMProvider(
        model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        canned_response="Answer with partial usage.",
        custom_usage=partial_usage,
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=llm_provider,
        telemetry=emitter,
    )

    response = await rag_service.generate_answer(
        principal=principal,
        request=RAGRequest(query="Question?", top_k=1),
    )
    assert response.usage is not None
    assert response.usage.input_tokens == 150
    assert response.usage.output_tokens is None

    # Available input tokens emitted for generation, output tokens omitted for generation
    assert any(
        m.metric_name == "InputTokens"
        and m.value == 150.0
        and m.dimensions.get("Operation") == "rag_generate_answer"
        for m in emitter.metrics
    )
    assert not any(
        m.metric_name == "OutputTokens"
        and m.dimensions.get("Operation") == "rag_generate_answer"
        for m in emitter.metrics
    )

    # Call coverage: 1 unmeasured generation model call, 1 measured embedding call
    assert (
        len([
            m
            for m in emitter.metrics
            if m.metric_name == "CostUnmeasuredModelCalls"
            and m.dimensions.get("Operation") == "rag_generate_answer"
        ])
        == 1
    )
    assert (
        len([
            m
            for m in emitter.metrics
            if m.metric_name == "CostMeasuredModelCalls"
            and m.dimensions.get("Operation") == "rag_generate_answer"
        ])
        == 0
    )
    assert (
        len([
            m
            for m in emitter.metrics
            if m.metric_name == "CostMeasuredModelCalls"
            and m.dimensions.get("Operation") == "retrieval_search"
        ])
        == 1
    )

    # Query coverage: partial/unmeasured query, 0 measured queries
    assert len([m for m in emitter.metrics if m.metric_name == "CostUnmeasuredQueries"]) == 1
    assert len([m for m in emitter.metrics if m.metric_name == "CostPartialQueries"]) == 1
    assert len([m for m in emitter.metrics if m.metric_name == "CostMeasuredQueries"]) == 0


@pytest.mark.asyncio
async def test_rag_service_call_and_query_coverage_explicit_zero_counts() -> None:
    """When generation explicitly reports zero counts, it is measured as $0.00
    and contributes to query cost.
    """
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    embedding_provider = _ConfigurableEmbeddingProvider(
        model_id="amazon.titan-embed-text-v2:0",
        input_tokens=100,
    )
    vector_store = InMemoryVectorStore()
    retrieval_service = RetrievalService(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        telemetry=emitter,
    )
    doc_id = str(uuid4())
    await vector_store.upsert(
        [
            VectorRecord(
                chunk_id=str(uuid4()),
                document_id=doc_id,
                organization_id=str(principal.organization_id),
                content="Known evidence.",
                embedding=[0.1] * 1536,
                metadata={"confidentiality_level": "internal"},
            )
        ]
    )

    zero_usage = TokenUsage(input_tokens=0, output_tokens=0)
    llm_provider = _ConfigurableLLMProvider(
        model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        canned_response="Answer with zero usage.",
        custom_usage=zero_usage,
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=llm_provider,
        telemetry=emitter,
    )

    response = await rag_service.generate_answer(
        principal=principal,
        request=RAGRequest(query="Question?", top_k=1),
    )
    assert response.usage is not None
    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.usage.total_tokens == 0

    # Call coverage: 2 measured model calls (1 retrieval + 1 generation with $0.00 cost)
    model_cost_records = [
        m
        for m in emitter.metrics
        if m.metric_name == "ModelCallEstimatedCostUSD"
        and m.dimensions.get("Operation") == "rag_generate_answer"
    ]
    assert len(model_cost_records) == 1
    assert model_cost_records[0].value == 0.0
    assert (
        len([
            m
            for m in emitter.metrics
            if m.metric_name == "CostMeasuredModelCalls"
            and m.dimensions.get("Operation") == "rag_generate_answer"
        ])
        == 1
    )
    assert len([m for m in emitter.metrics if m.metric_name == "CostMeasuredModelCalls"]) == 2

    # Query coverage: 1 measured query with embedding cost + 0.00
    query_cost_records = [
        m for m in emitter.metrics if m.metric_name == "EstimatedInferenceCostUSD"
    ]
    assert len(query_cost_records) == 1
    assert query_cost_records[0].value == pytest.approx(0.000002)
    assert len([m for m in emitter.metrics if m.metric_name == "CostMeasuredQueries"]) == 1
    assert not any(m.metric_name == "CostUnmeasuredQueries" for m in emitter.metrics)


@pytest.mark.asyncio
async def test_rag_service_call_and_query_coverage_complete_counts_no_double_counting() -> None:
    """When both calls report complete counts, call and query costs are summed exactly once."""
    emitter = InMemoryTelemetryEmitter()
    principal = _make_principal()

    embedding_provider = _ConfigurableEmbeddingProvider(
        model_id="amazon.titan-embed-text-v2:0",
        input_tokens=1000,
    )
    vector_store = InMemoryVectorStore()
    retrieval_service = RetrievalService(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        telemetry=emitter,
    )
    doc_id = str(uuid4())
    await vector_store.upsert(
        [
            VectorRecord(
                chunk_id=str(uuid4()),
                document_id=doc_id,
                organization_id=str(principal.organization_id),
                content="Known evidence.",
                embedding=[0.1] * 1536,
                metadata={"confidentiality_level": "internal"},
            )
        ]
    )

    complete_usage = TokenUsage(input_tokens=100, output_tokens=50)
    llm_provider = _ConfigurableLLMProvider(
        model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        canned_response="Answer with complete usage.",
        custom_usage=complete_usage,
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_provider=llm_provider,
        telemetry=emitter,
    )

    response = await rag_service.generate_answer(
        principal=principal,
        request=RAGRequest(query="Question?", top_k=1),
    )
    assert response.usage is not None
    assert response.usage.input_tokens == 100
    assert response.usage.output_tokens == 50
    assert response.usage.total_tokens == 150

    # Call coverage: 2 measured model calls (1 retrieval embedding + 1 generation)
    model_call_records = [
        m for m in emitter.metrics if m.metric_name == "CostMeasuredModelCalls"
    ]
    assert len(model_call_records) == 2
    assert (
        len([
            m
            for m in emitter.metrics
            if m.metric_name == "CostMeasuredModelCalls"
            and m.dimensions.get("Operation") == "rag_generate_answer"
        ])
        == 1
    )
    gen_call_cost = [
        m
        for m in emitter.metrics
        if m.metric_name == "ModelCallEstimatedCostUSD"
        and m.dimensions.get("Operation") == "rag_generate_answer"
    ]
    assert len(gen_call_cost) == 1
    assert gen_call_cost[0].value == 0.00035

    # Query coverage: exactly 1 CostMeasuredQueries record
    query_count_records = [
        m for m in emitter.metrics if m.metric_name == "CostMeasuredQueries"
    ]
    assert len(query_count_records) == 1
    assert query_count_records[0].value == 1.0

    # Total query cost: Titan ($0.00002) + Claude Haiku ($0.00035) = $0.00037
    query_cost_records = [
        m for m in emitter.metrics if m.metric_name == "EstimatedInferenceCostUSD"
    ]
    assert len(query_cost_records) == 1
    assert query_cost_records[0].value == pytest.approx(0.00037)

    # No unmeasured query records, no double-counting
    assert not any(m.metric_name == "CostUnmeasuredQueries" for m in emitter.metrics)
