"""Unit tests for the bounded Research Agent."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.adapters.mock.llm import MockLLMProvider
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.core.errors import UpstreamError
from app.models.ai import (
    GenerationRequest,
    GenerationResponse,
    TokenUsage,
    VectorRecord,
)
from app.models.enums import Role
from app.models.principal import Principal
from app.models.research_agent import ResearchAgentRequest
from app.ports.llm_provider import LLMProvider
from app.services.research_agent import ResearchAgent
from app.services.retrieval import RetrievalService
from app.services.tool_registry import ToolRegistry

ORG_A = UUID("11111111-1111-4111-8111-111111111111")
ORG_B = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DOC_A = UUID("aaaaaaaa-1111-4111-8111-111111111111")
DOC_B = UUID("bbbbbbbb-2222-4222-8222-222222222222")


class _RecordingLLMProvider(LLMProvider):
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[GenerationRequest] = []

    @property
    def chat_model_id(self) -> str:
        return "mock:research-v1"

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.calls.append(request)

        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)

        return GenerationResponse(
            content=content,
            model_id=self.chat_model_id,
            usage=TokenUsage(input_tokens=20, output_tokens=10),
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
        email="researcher@cloudguard.ai",
        department="Security",
    )


def _agent(
    *,
    llm_provider: LLMProvider,
    vector_store: InMemoryVectorStore | None = None,
) -> ResearchAgent:
    store = vector_store or InMemoryVectorStore()
    retrieval = RetrievalService(
        embedding_provider=MockEmbeddingProvider(),
        vector_store=store,
    )
    registry = ToolRegistry(retrieval_service=retrieval)

    return ResearchAgent(
        llm_provider=llm_provider,
        tool_registry=registry,
    )


@pytest.mark.asyncio
async def test_research_agent_executes_one_bounded_search() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-a",
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="CloudGuard requires MFA for privileged access.",
                metadata={"confidentiality_level": "internal"},
            )
        ]
    )

    llm = _RecordingLLMProvider(
        {
            "tool_name": "search_documents",
            "arguments": {"query": "privileged access MFA", "top_k": 5},
        }
    )
    agent = _agent(llm_provider=llm, vector_store=store)

    result = await agent.research(
        principal=_principal(),
        request=ResearchAgentRequest(query="What controls govern privileged access?"),
    )

    assert result.retrieval_count == 1
    assert result.tool_calls_used == 1
    assert result.evidence[0].chunk_id == "chunk-a"
    assert result.model_id == "mock:research-v1"

    assert len(llm.calls) == 1
    generation_request = llm.calls[0]
    assert generation_request.temperature == 0.0
    assert generation_request.response_schema is not None
    assert generation_request.system_prompt is not None
    assert "application, not you" in generation_request.system_prompt.lower()


@pytest.mark.asyncio
async def test_research_agent_rejects_malformed_json() -> None:
    llm = _RecordingLLMProvider("not-json")
    agent = _agent(llm_provider=llm)

    with pytest.raises(UpstreamError, match="invalid tool intent"):
        await agent.research(
            principal=_principal(),
            request=ResearchAgentRequest(query="Find access policy evidence"),
        )


@pytest.mark.asyncio
async def test_research_agent_rejects_model_supplied_tenant_identity() -> None:
    llm = _RecordingLLMProvider(
        {
            "tool_name": "search_documents",
            "arguments": {
                "query": "policy",
                "organization_id": str(ORG_B),
            },
        }
    )
    agent = _agent(llm_provider=llm)

    with pytest.raises(UpstreamError, match="invalid tool intent"):
        await agent.research(
            principal=_principal(organization_id=ORG_A),
            request=ResearchAgentRequest(query="Find policy"),
        )


@pytest.mark.asyncio
async def test_research_agent_rejects_out_of_scope_tool() -> None:
    llm = _RecordingLLMProvider(
        {
            "tool_name": "delete_document",
            "arguments": {},
        }
    )
    agent = _agent(llm_provider=llm)

    with pytest.raises(UpstreamError, match="invalid tool intent"):
        await agent.research(
            principal=_principal(),
            request=ResearchAgentRequest(query="Delete the policy"),
        )


@pytest.mark.asyncio
async def test_research_agent_preserves_original_principal_tenant_scope() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-a",
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Shared control evidence",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="chunk-b",
                document_id=str(DOC_B),
                organization_id=str(ORG_B),
                embedding=[0.0] * 1024,
                content="Shared control evidence",
                metadata={"confidentiality_level": "internal"},
            ),
        ]
    )

    llm = _RecordingLLMProvider(
        {
            "tool_name": "search_documents",
            "arguments": {"query": "shared control evidence", "top_k": 10},
        }
    )
    agent = _agent(llm_provider=llm, vector_store=store)

    result = await agent.research(
        principal=_principal(organization_id=ORG_A),
        request=ResearchAgentRequest(query="Find shared control evidence"),
    )

    assert [match.chunk_id for match in result.evidence] == ["chunk-a"]
    assert result.tool_calls_used == 1


def test_research_agent_rejects_invalid_tool_budget() -> None:
    llm = _RecordingLLMProvider({})

    with pytest.raises(ValueError, match="max_tool_calls must be at least 1"):
        ResearchAgent(
            llm_provider=llm,
            tool_registry=ToolRegistry(
                retrieval_service=RetrievalService(
                    embedding_provider=MockEmbeddingProvider(),
                    vector_store=InMemoryVectorStore(),
                )
            ),
            max_tool_calls=0,
        )


@pytest.mark.asyncio
async def test_research_agent_rejects_top_k_above_agent_limit() -> None:
    llm = _RecordingLLMProvider(
        {
            "tool_name": "search_documents",
            "arguments": {
                "query": "policy",
                "top_k": 100,
            },
        }
    )
    agent = _agent(llm_provider=llm)

    with pytest.raises(UpstreamError, match="invalid tool intent"):
        await agent.research(
            principal=_principal(),
            request=ResearchAgentRequest(query="Find policy evidence"),
        )


@pytest.mark.asyncio
async def test_research_agent_preserves_original_principal_clearance_scope() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-internal",
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Shared research evidence",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="chunk-restricted",
                document_id=str(DOC_B),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Shared research evidence",
                metadata={"confidentiality_level": "restricted"},
            ),
        ]
    )

    llm = _RecordingLLMProvider(
        {
            "tool_name": "search_documents",
            "arguments": {
                "query": "shared research evidence",
                "top_k": 10,
            },
        }
    )
    agent = _agent(llm_provider=llm, vector_store=store)

    result = await agent.research(
        principal=_principal(role=Role.ANALYST),
        request=ResearchAgentRequest(query="Find shared research evidence"),
    )

    assert [match.chunk_id for match in result.evidence] == ["chunk-internal"]
    assert result.tool_calls_used == 1


class _UnexpectedFailureLLMProvider(LLMProvider):
    @property
    def chat_model_id(self) -> str:
        return "mock:unexpected-failure"

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        raise RuntimeError("provider implementation detail")


@pytest.mark.asyncio
async def test_research_agent_normalizes_unexpected_provider_failure() -> None:
    agent = _agent(llm_provider=_UnexpectedFailureLLMProvider())

    with pytest.raises(UpstreamError, match="research agent generation failed"):
        await agent.research(
            principal=_principal(),
            request=ResearchAgentRequest(query="Find access control evidence"),
        )


@pytest.mark.asyncio
async def test_research_agent_works_with_deterministic_mock_llm() -> None:
    llm = MockLLMProvider()
    agent = _agent(llm_provider=llm)

    result = await agent.research(
        principal=_principal(),
        request=ResearchAgentRequest(query="Find access control evidence"),
    )

    assert result.retrieval_count == 0
    assert result.tool_calls_used == 1
    assert result.evidence == []
    assert result.model_id == "mock:chat-v1"
    assert len(llm.generate_calls) == 1
