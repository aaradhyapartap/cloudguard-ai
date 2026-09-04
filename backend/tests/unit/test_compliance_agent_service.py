"""Unit tests for bounded Compliance Agent execution."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.core.errors import UpstreamError
from app.models.ai import (
    GenerationRequest,
    GenerationResponse,
    TokenUsage,
    VectorRecord,
)
from app.models.compliance import (
    ComplianceAssessmentResponse,
    ComplianceControlRead,
    ComplianceFrameworkRead,
)
from app.models.compliance_agent import ComplianceAgentRequest
from app.models.enums import (
    AssessmentStatus,
    ControlStatus,
    RiskClassification,
    Role,
)
from app.models.principal import Principal
from app.ports.compliance_repository import ComplianceRepository
from app.ports.llm_provider import LLMProvider
from app.services.compliance_agent import ComplianceAgent
from app.services.compliance_policy_read import CompliancePolicyReadService
from app.services.retrieval import RetrievalService
from app.services.tool_registry import ToolRegistry

ORG_A = UUID("11111111-1111-4111-8111-111111111111")
ORG_B = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ASSESSMENT_ID = UUID("33333333-3333-4333-8333-333333333333")
CONTROL_ID = UUID("44444444-4444-4444-8444-444444444444")
FRAMEWORK_ID = UUID("99999999-9999-4999-8999-999999999999")
DOC_A = UUID("55555555-5555-4555-8555-555555555555")
DOC_B = UUID("66666666-6666-4666-8666-666666666666")
CHUNK_A = UUID("77777777-7777-4777-8777-777777777777")
CHUNK_B = UUID("88888888-8888-4888-8888-888888888888")


class _SequencedLLMProvider(LLMProvider):
    def __init__(self, payloads: list[object]) -> None:
        self._payloads = list(payloads)
        self.calls: list[GenerationRequest] = []

    @property
    def chat_model_id(self) -> str:
        return "mock:compliance-v1"

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.calls.append(request)

        if not self._payloads:
            raise RuntimeError("Unexpected extra model call.")

        payload = self._payloads.pop(0)
        content = payload if isinstance(payload, str) else json.dumps(payload)
        call_number = len(self.calls)

        return GenerationResponse(
            content=content,
            model_id=f"mock:compliance-v{call_number}",
            usage=TokenUsage(
                input_tokens=call_number * 10,
                output_tokens=call_number * 5,
            ),
            stop_reason="end_turn",
            latency_ms=1,
            generated_at=datetime.now(UTC),
        )


def _principal(
    *,
    organization_id: UUID = ORG_A,
    role: Role = Role.ANALYST,
) -> Principal:
    return Principal(
        user_id=USER_ID,
        organization_id=organization_id,
        role=role,
        email="analyst@cloudguard.ai",
        department="Security",
    )


def _policy_repository() -> ComplianceRepository:
    repo = AsyncMock(spec=ComplianceRepository)

    repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=ASSESSMENT_ID,
        organization_id=ORG_A,
        framework_id=FRAMEWORK_ID,
        title="Compliance Assessment",
        status=AssessmentStatus.IN_PROGRESS,
        overall_score=Decimal("0.00"),
        risk_classification=RiskClassification.LOW,
        scoring_version="v1.0",
        created_by=USER_ID,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    repo.get_framework.return_value = ComplianceFrameworkRead(
        id=FRAMEWORK_ID,
        code="SOC2",
        name="SOC 2",
        version="2026.1",
        description="SOC 2 framework",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    repo.get_framework_controls.return_value = [
        ComplianceControlRead(
            id=CONTROL_ID,
            framework_id=FRAMEWORK_ID,
            control_code="CC6.1",
            title="Logical Access",
            description="Privileged access requires appropriate authentication controls.",
            category="Security",
            default_weight=Decimal("3.0"),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    ]

    return repo


def _agent(
    *,
    llm_provider: LLMProvider,
    vector_store: InMemoryVectorStore | None = None,
) -> ComplianceAgent:
    store = vector_store or InMemoryVectorStore()
    retrieval = RetrievalService(
        embedding_provider=MockEmbeddingProvider(),
        vector_store=store,
    )

    return ComplianceAgent(
        llm_provider=llm_provider,
        tool_registry=ToolRegistry(
            retrieval_service=retrieval,
            compliance_policy_read_service=CompliancePolicyReadService(
                repository=_policy_repository(),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_compliance_agent_executes_bounded_two_call_flow() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id=str(CHUNK_A),
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Privileged users must use MFA.",
                metadata={"confidentiality_level": "internal"},
            )
        ]
    )

    llm = _SequencedLLMProvider(
        [
            {
                "tool_name": "search_documents",
                "arguments": {
                    "query": "privileged access MFA",
                    "top_k": 5,
                },
            },
            {
                "findings": [
                    {
                        "control_id": str(CONTROL_ID),
                        "proposed_status": "satisfied",
                        "rationale": "The retrieved evidence demonstrates MFA.",
                        "evidence_sources": ["S1"],
                        "confidence": 0.95,
                    }
                ]
            },
        ]
    )
    agent = _agent(llm_provider=llm, vector_store=store)

    result = await agent.analyze(
        principal=_principal(),
        request=ComplianceAgentRequest(
            assessment_id=ASSESSMENT_ID,
            control_ids=[CONTROL_ID],
            query_hint="MFA evidence",
            top_k=5,
        ),
    )

    assert result.retrieval_count == 1
    assert result.tool_calls_used == 2
    assert result.planning_model_id == "mock:compliance-v1"
    assert result.evaluation_model_id == "mock:compliance-v2"
    assert result.planning_usage.input_tokens == 10
    assert result.planning_usage.output_tokens == 5
    assert result.evaluation_usage.input_tokens == 20
    assert result.evaluation_usage.output_tokens == 10

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.control_id == CONTROL_ID
    assert finding.proposed_status is ControlStatus.SATISFIED
    assert finding.confidence == 0.95
    assert len(finding.evidence_references) == 1
    assert finding.evidence_references[0].chunk_id == CHUNK_A
    assert finding.evidence_references[0].document_id == DOC_A
    assert finding.evidence_references[0].quote == "Privileged users must use MFA."

    assert len(llm.calls) == 2
    assert llm.calls[0].temperature == 0.0
    assert llm.calls[1].temperature == 0.0
    assert llm.calls[0].response_schema is not None
    assert llm.calls[1].response_schema is not None

    planning_prompt = llm.calls[0].messages[0].content
    evaluation_prompt = llm.calls[1].messages[0].content

    assert "Logical Access" in planning_prompt
    assert (
        "Privileged access requires appropriate authentication controls."
        in planning_prompt
    )
    assert "Logical Access" in evaluation_prompt
    assert (
        "Privileged access requires appropriate authentication controls."
        in evaluation_prompt
    )


@pytest.mark.asyncio
async def test_compliance_agent_preserves_original_principal_tenant_scope() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id=str(CHUNK_A),
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Shared compliance evidence",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id=str(CHUNK_B),
                document_id=str(DOC_B),
                organization_id=str(ORG_B),
                embedding=[0.0] * 1024,
                content="Shared compliance evidence",
                metadata={"confidentiality_level": "internal"},
            ),
        ]
    )

    llm = _SequencedLLMProvider(
        [
            {
                "tool_name": "search_documents",
                "arguments": {
                    "query": "shared compliance evidence",
                    "top_k": 10,
                },
            },
            {
                "findings": [
                    {
                        "control_id": str(CONTROL_ID),
                        "proposed_status": "satisfied",
                        "rationale": "Evidence found.",
                        "evidence_sources": ["S1"],
                    }
                ]
            },
        ]
    )
    agent = _agent(llm_provider=llm, vector_store=store)

    result = await agent.analyze(
        principal=_principal(organization_id=ORG_A),
        request=ComplianceAgentRequest(
            assessment_id=ASSESSMENT_ID,
            control_ids=[CONTROL_ID],
        ),
    )

    assert result.retrieval_count == 1
    assert result.findings[0].evidence_references[0].chunk_id == CHUNK_A


@pytest.mark.asyncio
async def test_compliance_agent_preserves_original_principal_clearance() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id=str(CHUNK_A),
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Shared clearance evidence",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id=str(CHUNK_B),
                document_id=str(DOC_B),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Shared clearance evidence",
                metadata={"confidentiality_level": "restricted"},
            ),
        ]
    )

    llm = _SequencedLLMProvider(
        [
            {
                "tool_name": "search_documents",
                "arguments": {
                    "query": "shared clearance evidence",
                    "top_k": 10,
                },
            },
            {
                "findings": [
                    {
                        "control_id": str(CONTROL_ID),
                        "proposed_status": "satisfied",
                        "rationale": "Evidence found.",
                        "evidence_sources": ["S1"],
                    }
                ]
            },
        ]
    )
    agent = _agent(llm_provider=llm, vector_store=store)

    result = await agent.analyze(
        principal=_principal(role=Role.ANALYST),
        request=ComplianceAgentRequest(
            assessment_id=ASSESSMENT_ID,
            control_ids=[CONTROL_ID],
        ),
    )

    assert result.retrieval_count == 1
    assert result.findings[0].evidence_references[0].chunk_id == CHUNK_A


@pytest.mark.asyncio
async def test_compliance_agent_rejects_malformed_planning_json() -> None:
    llm = _SequencedLLMProvider(["not-json"])
    agent = _agent(llm_provider=llm)

    with pytest.raises(UpstreamError, match="invalid tool intent"):
        await agent.analyze(
            principal=_principal(),
            request=ComplianceAgentRequest(
                assessment_id=ASSESSMENT_ID,
            ),
        )


@pytest.mark.asyncio
async def test_compliance_agent_rejects_out_of_scope_planning_tool() -> None:
    llm = _SequencedLLMProvider(
        [
            {
                "tool_name": "delete_document",
                "arguments": {},
            }
        ]
    )
    agent = _agent(llm_provider=llm)

    with pytest.raises(UpstreamError, match="invalid tool intent"):
        await agent.analyze(
            principal=_principal(),
            request=ComplianceAgentRequest(
                assessment_id=ASSESSMENT_ID,
            ),
        )


@pytest.mark.asyncio
async def test_compliance_agent_rejects_unknown_evidence_label() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id=str(CHUNK_A),
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Known compliance evidence",
                metadata={"confidentiality_level": "internal"},
            )
        ]
    )

    llm = _SequencedLLMProvider(
        [
            {
                "tool_name": "search_documents",
                "arguments": {
                    "query": "known compliance evidence",
                    "top_k": 5,
                },
            },
            {
                "findings": [
                    {
                        "control_id": str(CONTROL_ID),
                        "proposed_status": "satisfied",
                        "rationale": "Claim based on an invented source.",
                        "evidence_sources": ["S99"],
                    }
                ]
            },
        ]
    )
    agent = _agent(llm_provider=llm, vector_store=store)

    with pytest.raises(UpstreamError, match="invalid or untrusted findings"):
        await agent.analyze(
            principal=_principal(),
            request=ComplianceAgentRequest(
                assessment_id=ASSESSMENT_ID,
                control_ids=[CONTROL_ID],
            ),
        )


@pytest.mark.asyncio
async def test_compliance_agent_rejects_duplicate_control_findings() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id=str(CHUNK_A),
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Compliance evidence",
                metadata={"confidentiality_level": "internal"},
            )
        ]
    )

    llm = _SequencedLLMProvider(
        [
            {
                "tool_name": "search_documents",
                "arguments": {
                    "query": "compliance evidence",
                    "top_k": 5,
                },
            },
            {
                "findings": [
                    {
                        "control_id": str(CONTROL_ID),
                        "proposed_status": "satisfied",
                        "rationale": "First finding.",
                        "evidence_sources": ["S1"],
                    },
                    {
                        "control_id": str(CONTROL_ID),
                        "proposed_status": "deficient",
                        "rationale": "Duplicate finding.",
                        "evidence_sources": ["S1"],
                    },
                ]
            },
        ]
    )
    agent = _agent(llm_provider=llm, vector_store=store)

    with pytest.raises(UpstreamError, match="invalid or untrusted findings"):
        await agent.analyze(
            principal=_principal(),
            request=ComplianceAgentRequest(
                assessment_id=ASSESSMENT_ID,
                control_ids=[CONTROL_ID],
            ),
        )


@pytest.mark.asyncio
async def test_compliance_agent_zero_evidence_skips_evaluation() -> None:
    llm = _SequencedLLMProvider(
        [
            {
                "tool_name": "search_documents",
                "arguments": {
                    "query": "evidence that does not exist",
                    "top_k": 5,
                },
            }
        ]
    )
    agent = _agent(llm_provider=llm)

    result = await agent.analyze(
        principal=_principal(),
        request=ComplianceAgentRequest(
            assessment_id=ASSESSMENT_ID,
            control_ids=[CONTROL_ID],
        ),
    )

    assert result.findings == []
    assert result.retrieval_count == 0
    assert result.tool_calls_used == 2
    assert len(llm.calls) == 1
    assert result.planning_usage.input_tokens == 10
    assert result.evaluation_usage.input_tokens == 0
    assert result.evaluation_usage.output_tokens == 0


def test_compliance_agent_requires_exactly_two_tool_calls() -> None:
    llm = _SequencedLLMProvider([])

    with pytest.raises(ValueError, match="exactly two tool calls"):
        ComplianceAgent(
            llm_provider=llm,
            tool_registry=ToolRegistry(
                retrieval_service=RetrievalService(
                    embedding_provider=MockEmbeddingProvider(),
                    vector_store=InMemoryVectorStore(),
                ),
                compliance_policy_read_service=CompliancePolicyReadService(
                    repository=_policy_repository(),
                ),
            ),
            max_tool_calls=1,
        )
