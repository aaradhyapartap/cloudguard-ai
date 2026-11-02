"""Unit tests for deterministic Phase 6 agent workflow orchestration."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.adapters.local.rate_limit_store import InMemoryRateLimitStore
from app.adapters.local.work_lease_store import InMemoryWorkLeaseStore
from app.core.errors import AuthorizationError, RateLimitError, UpstreamError
from app.models.agent_workflow import (
    AgentWorkflowRequest,
    AgentWorkflowResearchResult,
    AgentWorkflowStatus,
)
from app.models.ai import TokenUsage, VectorMatch
from app.models.enums import Role
from app.models.principal import Principal
from app.models.research_agent import ResearchAgentResult
from app.models.reviewer_agent import (
    ReviewDecision,
    ReviewerAgentResult,
)
from app.models.risk_agent import (
    RiskAgentResult,
    RiskEvidenceEstimate,
)
from app.ports.work_lease_store import WorkLeaseStore
from app.services.agent_workflow import AgentWorkflow
from app.services.work_admission import WorkAdmission, WorkKind
from pydantic import ValidationError

EXECUTION_ID = UUID("00000000-0000-4000-8000-000000000010")


def _principal(*, role: Role = Role.ANALYST) -> Principal:
    return Principal(
        user_id=UUID("00000000-0000-4000-8000-000000000001"),
        organization_id=UUID("00000000-0000-4000-8000-000000000002"),
        role=role,
        email="workflow@cloudguard.ai",
        department="Security",
    )


def _admission() -> WorkAdmission:
    return WorkAdmission(
        leases=InMemoryWorkLeaseStore(),
        rates=InMemoryRateLimitStore(),
    )


def _evidence() -> VectorMatch:
    return VectorMatch(
        chunk_id="chunk-1",
        document_id="doc-1",
        content="Trusted workflow evidence.",
        score=0.95,
    )


def _estimate() -> RiskEvidenceEstimate:
    return RiskEvidenceEstimate(
        chunk_id="chunk-1",
        likelihood=0.4,
        impact=0.7,
        rationale="Grounded in trusted evidence.",
    )


class RecordingResearchAgent:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.principals: list[Principal] = []

    async def research(self, *, principal: Principal, request: object) -> ResearchAgentResult:
        self.calls.append("Research")
        self.principals.append(principal)
        return ResearchAgentResult(
            evidence=[_evidence()],
            retrieval_count=1,
            tool_calls_used=1,
            model_id="mock:research",
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class RecordingRiskAgent:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.principals: list[Principal] = []

    async def assess(self, *, principal: Principal, request: object) -> RiskAgentResult:
        self.calls.append("Risk")
        self.principals.append(principal)
        return RiskAgentResult(
            estimates=[_estimate()],
            evidence_count=1,
            model_id="mock:risk",
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class RecordingReviewerAgent:
    def __init__(
        self,
        calls: list[str],
        *,
        decision: ReviewDecision,
    ) -> None:
        self.calls = calls
        self.principals: list[Principal] = []
        self.decision = decision

    async def review(
        self,
        *,
        principal: Principal,
        request: object,
    ) -> ReviewerAgentResult:
        self.calls.append("Reviewer")
        self.principals.append(principal)
        return ReviewerAgentResult(
            decision=self.decision,
            reasons=(
                []
                if self.decision is ReviewDecision.PASS
                else [{"message": "Reviewer rejected workflow.", "chunk_ids": []}]
            ),
            evidence_count=1,
            model_id="mock:reviewer",
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


@pytest.mark.asyncio
async def test_workflow_executes_fixed_graph_in_order() -> None:
    calls: list[str] = []
    workflow = AgentWorkflow(
        research_agent=RecordingResearchAgent(calls),
        risk_agent=RecordingRiskAgent(calls),
        reviewer_agent=RecordingReviewerAgent(
            calls,
            decision=ReviewDecision.PASS,
        ),
        enabled=True,
        admission=_admission(),
    )

    result = await workflow.run(
        principal=_principal(),
        request=AgentWorkflowRequest(
            execution_id=EXECUTION_ID,
            correlation_id="corr-1",
            question="What is the risk?",
        ),
    )

    assert calls == ["Research", "Risk", "Reviewer"]
    assert result.status is AgentWorkflowStatus.SUCCEEDED
    assert result.failed_step is None


@pytest.mark.asyncio
async def test_workflow_preserves_original_principal_across_all_steps() -> None:
    calls: list[str] = []
    principal = _principal()
    research = RecordingResearchAgent(calls)
    risk = RecordingRiskAgent(calls)
    reviewer = RecordingReviewerAgent(
        calls,
        decision=ReviewDecision.PASS,
    )
    workflow = AgentWorkflow(
        research_agent=research,
        risk_agent=risk,
        reviewer_agent=reviewer,
        enabled=True,
        admission=_admission(),
    )

    await workflow.run(
        principal=principal,
        request=AgentWorkflowRequest(
            execution_id=EXECUTION_ID,
            correlation_id="corr-2",
            question="Review this workflow.",
        ),
    )

    assert research.principals == [principal]
    assert risk.principals == [principal]
    assert reviewer.principals == [principal]


@pytest.mark.asyncio
async def test_reviewer_fail_terminates_workflow_as_failed() -> None:
    calls: list[str] = []
    workflow = AgentWorkflow(
        research_agent=RecordingResearchAgent(calls),
        risk_agent=RecordingRiskAgent(calls),
        reviewer_agent=RecordingReviewerAgent(
            calls,
            decision=ReviewDecision.FAIL,
        ),
        enabled=True,
        admission=_admission(),
    )

    result = await workflow.run(
        principal=_principal(),
        request=AgentWorkflowRequest(
            execution_id=EXECUTION_ID,
            correlation_id="corr-3",
            question="Review this workflow.",
        ),
    )

    assert calls == ["Research", "Risk", "Reviewer"]
    assert result.status is AgentWorkflowStatus.FAILED
    assert result.failed_step == "Reviewer"
    assert result.reviewer.decision is ReviewDecision.FAIL


@pytest.mark.asyncio
async def test_disabled_workflow_blocks_all_agent_execution() -> None:
    calls: list[str] = []
    workflow = AgentWorkflow(
        research_agent=RecordingResearchAgent(calls),
        risk_agent=RecordingRiskAgent(calls),
        reviewer_agent=RecordingReviewerAgent(
            calls,
            decision=ReviewDecision.PASS,
        ),
        enabled=False,
        admission=_admission(),
    )

    with pytest.raises(RuntimeError, match="Agentic workflows are disabled"):
        await workflow.run(
            principal=_principal(),
            request=AgentWorkflowRequest(
                execution_id=EXECUTION_ID,
                correlation_id="corr-4",
                question="Review this workflow.",
            ),
        )

    assert calls == []


@pytest.mark.asyncio
async def test_workflow_preserves_execution_and_correlation_ids() -> None:
    calls: list[str] = []
    workflow = AgentWorkflow(
        research_agent=RecordingResearchAgent(calls),
        risk_agent=RecordingRiskAgent(calls),
        reviewer_agent=RecordingReviewerAgent(
            calls,
            decision=ReviewDecision.PASS,
        ),
        enabled=True,
        admission=_admission(),
    )

    result = await workflow.run(
        principal=_principal(),
        request=AgentWorkflowRequest(
            execution_id=EXECUTION_ID,
            correlation_id="corr-preserved",
            question="Review this workflow.",
        ),
    )

    assert result.execution_id == EXECUTION_ID
    assert result.correlation_id == "corr-preserved"

def test_workflow_research_projection_strips_arbitrary_metadata() -> None:
    source = ResearchAgentResult(
        evidence=[
            VectorMatch(
                chunk_id="chunk-1",
                document_id="doc-1",
                content="Trusted bounded evidence.",
                score=0.95,
                metadata={"arbitrary": "x" * 5000},
            )
        ],
        retrieval_count=1,
        tool_calls_used=1,
        model_id="mock:research",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )

    projected = AgentWorkflowResearchResult.from_research_result(source)
    dumped = projected.model_dump(mode="json")

    assert "metadata" not in dumped["evidence"][0]

    restored = projected.to_vector_matches()
    assert restored[0].metadata == {}


def test_workflow_research_projection_rejects_oversized_content() -> None:
    source = ResearchAgentResult(
        evidence=[
            VectorMatch(
                chunk_id="chunk-1",
                document_id="doc-1",
                content="x" * 1001,
                score=0.95,
            )
        ],
        retrieval_count=1,
        tool_calls_used=1,
        model_id="mock:research",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )

    with pytest.raises(ValidationError):
        AgentWorkflowResearchResult.from_research_result(source)


def test_workflow_research_projection_rejects_more_than_ten_evidence_items() -> None:
    evidence = [
        VectorMatch(
            chunk_id=f"chunk-{index}",
            document_id="doc-1",
            content="Trusted bounded evidence.",
            score=0.95,
        )
        for index in range(11)
    ]
    source = ResearchAgentResult(
        evidence=evidence,
        retrieval_count=11,
        tool_calls_used=1,
        model_id="mock:research",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )

    with pytest.raises(ValidationError):
        AgentWorkflowResearchResult.from_research_result(source)


@pytest.mark.asyncio
async def test_workflow_enforces_work_admission_when_configured() -> None:
    calls: list[str] = []
    leases = InMemoryWorkLeaseStore()
    rates = InMemoryRateLimitStore()
    admission = WorkAdmission(leases=leases, rates=rates)
    principal = _principal()

    workflow = AgentWorkflow(
        research_agent=RecordingResearchAgent(calls),
        risk_agent=RecordingRiskAgent(calls),
        reviewer_agent=RecordingReviewerAgent(
            calls,
            decision=ReviewDecision.PASS,
        ),
        enabled=True,
        admission=admission,
    )

    async with admission.admit(
        organization_id=principal.organization_id,
        kind=WorkKind.MODEL,
    ), admission.admit(
        organization_id=principal.organization_id,
        kind=WorkKind.MODEL,
    ):
        with pytest.raises(RateLimitError):
            await workflow.run(
                principal=principal,
                request=AgentWorkflowRequest(
                    execution_id=EXECUTION_ID,
                    correlation_id="corr-throttled",
                    question="Review this workflow.",
                ),
            )

    assert calls == []


@pytest.mark.asyncio
async def test_workflow_rejects_admin_before_admission_or_research() -> None:
    calls: list[str] = []

    class ExplodingLeaseStore(WorkLeaseStore):
        async def try_acquire(
            self, *, key: str, owner: str, now: int, expires_at: int
        ) -> bool:
            del key, owner, now, expires_at
            raise AssertionError("Admin must be rejected before admission lease acquisition.")

        async def release(self, *, key: str, owner: str) -> bool:
            del key, owner
            raise AssertionError("Admin must not release a slot.")

    workflow = AgentWorkflow(
        research_agent=RecordingResearchAgent(calls),
        risk_agent=RecordingRiskAgent(calls),
        reviewer_agent=RecordingReviewerAgent(
            calls,
            decision=ReviewDecision.PASS,
        ),
        enabled=True,
        admission=WorkAdmission(
            leases=ExplodingLeaseStore(),
            rates=InMemoryRateLimitStore(),
        ),
    )

    with pytest.raises(AuthorizationError):
        await workflow.run(
            principal=_principal(role=Role.ADMIN),
            request=AgentWorkflowRequest(
                execution_id=EXECUTION_ID,
                correlation_id="corr-admin-denied",
                question="Admin cannot trigger model workflows.",
            ),
        )

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.ANALYST, Role.MANAGER])
async def test_workflow_allows_authorized_roles_with_required_admission(role: Role) -> None:
    calls: list[str] = []
    workflow = AgentWorkflow(
        research_agent=RecordingResearchAgent(calls),
        risk_agent=RecordingRiskAgent(calls),
        reviewer_agent=RecordingReviewerAgent(
            calls,
            decision=ReviewDecision.PASS,
        ),
        enabled=True,
        admission=_admission(),
    )

    result = await workflow.run(
        principal=_principal(role=role),
        request=AgentWorkflowRequest(
            execution_id=EXECUTION_ID,
            correlation_id=f"corr-{role.value}",
            question="Authorized workflow execution.",
        ),
    )

    assert result.status is AgentWorkflowStatus.SUCCEEDED
    assert calls == ["Research", "Risk", "Reviewer"]


@pytest.mark.asyncio
async def test_workflow_fails_closed_when_admission_store_unavailable() -> None:
    calls: list[str] = []

    class OutageLeaseStore(WorkLeaseStore):
        async def try_acquire(
            self, *, key: str, owner: str, now: int, expires_at: int
        ) -> bool:
            del key, owner, now, expires_at
            raise RuntimeError("lease store unavailable")

        async def release(self, *, key: str, owner: str) -> bool:
            del key, owner
            return False

    workflow = AgentWorkflow(
        research_agent=RecordingResearchAgent(calls),
        risk_agent=RecordingRiskAgent(calls),
        reviewer_agent=RecordingReviewerAgent(
            calls,
            decision=ReviewDecision.PASS,
        ),
        enabled=True,
        admission=WorkAdmission(
            leases=OutageLeaseStore(),
            rates=InMemoryRateLimitStore(),
        ),
    )

    with pytest.raises(UpstreamError, match="Worker admission is temporarily unavailable"):
        await workflow.run(
            principal=_principal(role=Role.ANALYST),
            request=AgentWorkflowRequest(
                execution_id=EXECUTION_ID,
                correlation_id="corr-outage",
                question="Outage must fail closed.",
            ),
        )

    assert calls == []
