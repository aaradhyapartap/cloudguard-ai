"""Deterministic local orchestration for the Phase 6 agent workflow."""

from __future__ import annotations

from typing import Protocol

from app.models.agent_workflow import (
    AgentWorkflowRequest,
    AgentWorkflowResearchResult,
    AgentWorkflowResult,
    AgentWorkflowStatus,
)
from app.models.principal import Principal
from app.models.research_agent import ResearchAgentRequest, ResearchAgentResult
from app.models.reviewer_agent import (
    ReviewDecision,
    ReviewerAgentRequest,
    ReviewerAgentResult,
)
from app.models.risk_agent import RiskAgentRequest, RiskAgentResult


class ResearchAgentPort(Protocol):
    """Research capability required by the fixed workflow."""

    async def research(
        self,
        *,
        principal: Principal,
        request: ResearchAgentRequest,
    ) -> ResearchAgentResult: ...


class RiskAgentPort(Protocol):
    """Risk capability required by the fixed workflow."""

    async def assess(
        self,
        *,
        principal: Principal,
        request: RiskAgentRequest,
    ) -> RiskAgentResult: ...


class ReviewerAgentPort(Protocol):
    """Reviewer capability required by the fixed workflow."""

    async def review(
        self,
        *,
        principal: Principal,
        request: ReviewerAgentRequest,
    ) -> ReviewerAgentResult: ...


class AgentWorkflow:
    """Execute the fixed Research -> Risk -> Reviewer graph."""

    def __init__(
        self,
        *,
        research_agent: ResearchAgentPort,
        risk_agent: RiskAgentPort,
        reviewer_agent: ReviewerAgentPort,
        enabled: bool,
    ) -> None:
        self._research_agent = research_agent
        self._risk_agent = risk_agent
        self._reviewer_agent = reviewer_agent
        self._enabled = enabled

    async def run(
        self,
        *,
        principal: Principal,
        request: AgentWorkflowRequest,
    ) -> AgentWorkflowResult:
        """Execute the fixed graph while preserving the original human Principal."""
        if not self._enabled:
            raise RuntimeError("Agentic workflows are disabled.")

        research_result = await self._research_agent.research(
            principal=principal,
            request=ResearchAgentRequest(query=request.question),
        )

        risk_result = await self._risk_agent.assess(
            principal=principal,
            request=RiskAgentRequest(
                question=request.question,
                evidence=research_result.evidence,
            ),
        )

        reviewer_result = await self._reviewer_agent.review(
            principal=principal,
            request=ReviewerAgentRequest(
                question=request.question,
                evidence=research_result.evidence,
                risk_estimates=risk_result.estimates,
            ),
        )

        status = (
            AgentWorkflowStatus.SUCCEEDED
            if reviewer_result.decision is ReviewDecision.PASS
            else AgentWorkflowStatus.FAILED
        )
        failed_step = (
            None
            if reviewer_result.decision is ReviewDecision.PASS
            else "Reviewer"
        )

        return AgentWorkflowResult(
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            status=status,
            failed_step=failed_step,
            research=AgentWorkflowResearchResult.from_research_result(
                research_result
            ),
            risk=risk_result,
            reviewer=reviewer_result,
        )
