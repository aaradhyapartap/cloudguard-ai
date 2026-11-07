"""Unit tests for the bounded Principal-aware ToolRegistry."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.models.agents import (
    AgentType,
    PolicyReadResult,
    ToolCallRequest,
    ToolName,
)
from app.models.ai import VectorRecord
from app.models.compliance import (
    ComplianceAssessmentResponse,
    ComplianceControlRead,
    ComplianceFrameworkRead,
)
from app.models.enums import (
    AssessmentStatus,
    RiskClassification,
    Role,
)
from app.models.principal import Principal
from app.models.retrieval import RetrievalResponse
from app.ports.compliance_repository import ComplianceRepository
from app.services.compliance_policy_read import CompliancePolicyReadService
from app.services.retrieval import RetrievalService
from app.services.tool_registry import ToolExecutionBudget, ToolRegistry

ORG_A = UUID("11111111-1111-4111-8111-111111111111")
ORG_B = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DOC_A = UUID("aaaaaaaa-1111-4111-8111-111111111111")
DOC_B = UUID("bbbbbbbb-2222-4222-8222-222222222222")


def _principal(
    *,
    organization_id: UUID = ORG_A,
    role: Role = Role.ANALYST,
) -> Principal:
    return Principal(
        user_id=USER_ID,
        organization_id=organization_id,
        role=role,
        email="user@cloudguard.ai",
        department="Security",
    )


def _registry(
    vector_store: InMemoryVectorStore | None = None,
) -> ToolRegistry:
    store = vector_store or InMemoryVectorStore()
    retrieval = RetrievalService(
        embedding_provider=MockEmbeddingProvider(),
        vector_store=store,
    )
    return ToolRegistry(retrieval_service=retrieval)


@pytest.mark.asyncio
async def test_research_can_search_documents() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-a",
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="CloudGuard tenant isolation policy",
                metadata={"confidentiality_level": "internal"},
            )
        ]
    )

    registry = _registry(store)
    budget = ToolExecutionBudget()

    result = await registry.invoke(
        agent=AgentType.RESEARCH,
        principal=_principal(),
        budget=budget,
        request=ToolCallRequest(
            tool_name=ToolName.SEARCH_DOCUMENTS,
            arguments={"query": "tenant isolation", "top_k": 5},
        ),
    )

    assert result.tool_name is ToolName.SEARCH_DOCUMENTS
    assert isinstance(result.result, RetrievalResponse)
    assert result.result.total == 1
    assert result.result.matches[0].chunk_id == "chunk-a"
    assert budget.used_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [AgentType.RISK, AgentType.REVIEWER])
async def test_non_research_agents_cannot_search_documents(agent: AgentType) -> None:
    registry = _registry()
    budget = ToolExecutionBudget()

    with pytest.raises(AuthorizationError):
        await registry.invoke(
            agent=agent,
            principal=_principal(),
            budget=budget,
            request=ToolCallRequest(
                tool_name=ToolName.SEARCH_DOCUMENTS,
                arguments={"query": "policy"},
            ),
        )

    assert budget.used_calls == 0


@pytest.mark.asyncio
async def test_malformed_arguments_fail_before_execution() -> None:
    registry = _registry()
    budget = ToolExecutionBudget()

    with pytest.raises(ValidationError, match="tool arguments are invalid"):
        await registry.invoke(
            agent=AgentType.RESEARCH,
            principal=_principal(),
            budget=budget,
            request=ToolCallRequest(
                tool_name=ToolName.SEARCH_DOCUMENTS,
                arguments={
                    "query": "policy",
                    "organization_id": str(ORG_B),
                },
            ),
        )

    assert budget.used_calls == 1


@pytest.mark.asyncio
async def test_original_principal_controls_tenant_scope() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-a",
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Shared policy",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="chunk-b",
                document_id=str(DOC_B),
                organization_id=str(ORG_B),
                embedding=[0.0] * 1024,
                content="Shared policy",
                metadata={"confidentiality_level": "internal"},
            ),
        ]
    )

    registry = _registry(store)
    budget = ToolExecutionBudget()

    result = await registry.invoke(
        agent=AgentType.RESEARCH,
        principal=_principal(organization_id=ORG_A),
        budget=budget,
        request=ToolCallRequest(
            tool_name=ToolName.SEARCH_DOCUMENTS,
            arguments={"query": "shared policy", "top_k": 10},
        ),
    )

    assert isinstance(result.result, RetrievalResponse)
    assert [match.chunk_id for match in result.result.matches] == ["chunk-a"]
    assert budget.used_calls == 1


@pytest.mark.asyncio
async def test_tool_call_budget_is_enforced() -> None:
    registry = _registry()
    budget = ToolExecutionBudget(max_calls=1)
    request = ToolCallRequest(
        tool_name=ToolName.SEARCH_DOCUMENTS,
        arguments={"query": "policy"},
    )

    await registry.invoke(
        agent=AgentType.RESEARCH,
        principal=_principal(),
        budget=budget,
        request=request,
    )

    with pytest.raises(ValidationError, match="budget has been exhausted"):
        await registry.invoke(
            agent=AgentType.RESEARCH,
            principal=_principal(),
            budget=budget,
            request=request,
        )

    assert budget.used_calls == 1


def test_tool_execution_budget_rejects_invalid_max_calls() -> None:
    with pytest.raises(ValueError, match="max_calls must be at least 1"):
        ToolExecutionBudget(max_calls=0)


def test_tool_execution_budgets_are_independent() -> None:
    first = ToolExecutionBudget(max_calls=1)
    second = ToolExecutionBudget(max_calls=1)

    first.consume()

    assert first.used_calls == 1
    assert second.used_calls == 0


def test_unknown_tool_name_is_rejected_by_request_schema() -> None:
    with pytest.raises(ValueError):
        ToolCallRequest(
            tool_name="delete_document",
            arguments={},
        )


@pytest.mark.asyncio
async def test_failed_tool_attempt_consumes_budget() -> None:
    registry = _registry()
    budget = ToolExecutionBudget(max_calls=1)

    with pytest.raises(ValidationError, match="tool arguments are invalid"):
        await registry.invoke(
            agent=AgentType.RESEARCH,
            principal=_principal(),
            budget=budget,
            request=ToolCallRequest(
                tool_name=ToolName.SEARCH_DOCUMENTS,
                arguments={
                    "query": "policy",
                    "organization_id": str(ORG_B),
                },
            ),
        )

    assert budget.used_calls == 1

    with pytest.raises(ValidationError, match="budget has been exhausted"):
        await registry.invoke(
            agent=AgentType.RESEARCH,
            principal=_principal(),
            budget=budget,
            request=ToolCallRequest(
                tool_name=ToolName.SEARCH_DOCUMENTS,
                arguments={"query": "policy"},
            ),
        )


@pytest.mark.asyncio
async def test_original_principal_controls_clearance_scope() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-internal",
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Shared clearance policy",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="chunk-restricted",
                document_id=str(DOC_B),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Shared clearance policy",
                metadata={"confidentiality_level": "restricted"},
            ),
        ]
    )

    registry = _registry(store)
    budget = ToolExecutionBudget()

    result = await registry.invoke(
        agent=AgentType.RESEARCH,
        principal=_principal(role=Role.ANALYST),
        budget=budget,
        request=ToolCallRequest(
            tool_name=ToolName.SEARCH_DOCUMENTS,
            arguments={"query": "shared clearance policy", "top_k": 10},
        ),
    )

    assert isinstance(result.result, RetrievalResponse)
    assert [match.chunk_id for match in result.result.matches] == ["chunk-internal"]
    assert budget.used_calls == 1
@pytest.mark.asyncio
async def test_compliance_agent_can_search_documents() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-compliance",
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Access control policy requires quarterly review.",
                metadata={"confidentiality_level": "internal"},
            )
        ]
    )

    registry = _registry(store)
    budget = ToolExecutionBudget(max_calls=1)

    result = await registry.invoke(
        agent=AgentType.COMPLIANCE,
        principal=_principal(role=Role.ANALYST),
        request=ToolCallRequest(
            tool_name=ToolName.SEARCH_DOCUMENTS,
            arguments={
                "query": "access control policy",
                "top_k": 3,
            },
        ),
        budget=budget,
    )

    assert result.tool_name is ToolName.SEARCH_DOCUMENTS
    assert isinstance(result.result, RetrievalResponse)
    assert result.result.total == 1
    assert result.result.matches[0].chunk_id == "chunk-compliance"
    assert budget.used_calls == 1


@pytest.mark.asyncio
async def test_compliance_agent_search_respects_original_principal_tenant() -> None:
    store = InMemoryVectorStore()
    await store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-org-a",
                document_id=str(DOC_A),
                organization_id=str(ORG_A),
                embedding=[0.0] * 1024,
                content="Shared compliance policy",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="chunk-org-b",
                document_id=str(DOC_B),
                organization_id=str(ORG_B),
                embedding=[0.0] * 1024,
                content="Shared compliance policy",
                metadata={"confidentiality_level": "internal"},
            ),
        ]
    )

    registry = _registry(store)
    budget = ToolExecutionBudget(max_calls=1)

    result = await registry.invoke(
        agent=AgentType.COMPLIANCE,
        principal=_principal(
            organization_id=ORG_A,
            role=Role.ANALYST,
        ),
        request=ToolCallRequest(
            tool_name=ToolName.SEARCH_DOCUMENTS,
            arguments={
                "query": "shared compliance policy",
                "top_k": 10,
            },
        ),
        budget=budget,
    )

    assert isinstance(result.result, RetrievalResponse)
    assert [match.chunk_id for match in result.result.matches] == ["chunk-org-a"]
    assert budget.used_calls == 1
@pytest.mark.asyncio
async def test_compliance_agent_can_get_policy() -> None:
    repo = AsyncMock(spec=ComplianceRepository)
    assessment_id = UUID("44444444-4444-4444-8444-444444444444")
    framework_id = UUID("55555555-5555-4555-8555-555555555555")
    control_id = UUID("66666666-6666-4666-8666-666666666666")

    repo.get_assessment.return_value = ComplianceAssessmentResponse(
        id=assessment_id,
        organization_id=ORG_A,
        framework_id=framework_id,
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
        id=framework_id,
        code="SOC2",
        name="SOC 2",
        version="2026.1",
        description="SOC 2 framework",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo.get_framework_controls.return_value = [
        ComplianceControlRead(
            id=control_id,
            framework_id=framework_id,
            control_code="CC6.1",
            title="Logical Access",
            description="Logical access controls",
            category="Security",
            default_weight=Decimal("3.0"),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    ]

    registry = ToolRegistry(
        retrieval_service=RetrievalService(
            embedding_provider=MockEmbeddingProvider(),
            vector_store=InMemoryVectorStore(),
        ),
        compliance_policy_read_service=CompliancePolicyReadService(
            repository=repo,
        ),
    )
    budget = ToolExecutionBudget(max_calls=1)

    result = await registry.invoke(
        agent=AgentType.COMPLIANCE,
        principal=_principal(),
        request=ToolCallRequest(
            tool_name=ToolName.GET_POLICY,
            arguments={
                "assessment_id": str(assessment_id),
                "control_ids": [str(control_id)],
            },
        ),
        budget=budget,
    )

    assert result.tool_name is ToolName.GET_POLICY
    assert isinstance(result.result, PolicyReadResult)
    assert result.result.assessment_id == assessment_id
    assert result.result.framework.id == framework_id
    assert [control.id for control in result.result.controls] == [control_id]
    assert budget.used_calls == 1

    repo.get_assessment.assert_awaited_once_with(
        organization_id=ORG_A,
        assessment_id=assessment_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent",
    [
        AgentType.RESEARCH,
        AgentType.RISK,
        AgentType.REVIEWER,
    ],
)
async def test_non_compliance_agents_cannot_get_policy(
    agent: AgentType,
) -> None:
    registry = _registry()
    budget = ToolExecutionBudget(max_calls=1)

    with pytest.raises(AuthorizationError):
        await registry.invoke(
            agent=agent,
            principal=_principal(),
            request=ToolCallRequest(
                tool_name=ToolName.GET_POLICY,
                arguments={
                    "assessment_id": "44444444-4444-4444-8444-444444444444",
                },
            ),
            budget=budget,
        )

    assert budget.used_calls == 0


@pytest.mark.asyncio
async def test_get_policy_fails_closed_when_reader_is_not_configured() -> None:
    registry = _registry()
    budget = ToolExecutionBudget(max_calls=1)

    with pytest.raises(
        ValidationError,
        match="policy-read tool is not configured",
    ):
        await registry.invoke(
            agent=AgentType.COMPLIANCE,
            principal=_principal(),
            request=ToolCallRequest(
                tool_name=ToolName.GET_POLICY,
                arguments={
                    "assessment_id": "44444444-4444-4444-8444-444444444444",
                },
            ),
            budget=budget,
        )


@pytest.mark.asyncio
async def test_get_policy_uses_original_principal_tenant() -> None:
    repo = AsyncMock(spec=ComplianceRepository)
    assessment_id = UUID("44444444-4444-4444-8444-444444444444")
    repo.get_assessment.return_value = None

    registry = ToolRegistry(
        retrieval_service=RetrievalService(
            embedding_provider=MockEmbeddingProvider(),
            vector_store=InMemoryVectorStore(),
        ),
        compliance_policy_read_service=CompliancePolicyReadService(
            repository=repo,
        ),
    )

    with pytest.raises(NotFoundError):
        await registry.invoke(
            agent=AgentType.COMPLIANCE,
            principal=_principal(organization_id=ORG_A),
            request=ToolCallRequest(
                tool_name=ToolName.GET_POLICY,
                arguments={
                    "assessment_id": str(assessment_id),
                },
            ),
            budget=ToolExecutionBudget(max_calls=1),
        )

    repo.get_assessment.assert_awaited_once_with(
        organization_id=ORG_A,
        assessment_id=assessment_id,
    )
