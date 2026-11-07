"""Provider-neutral contracts for the deterministic Phase 6 agent workflow."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ai import TokenUsage, VectorMatch
from app.models.research_agent import ResearchAgentResult
from app.models.reviewer_agent import ReviewerAgentResult
from app.models.risk_agent import RiskAgentResult


class AgentWorkflowEvidence(BaseModel):
    """Metadata-free evidence safe to carry through Step Functions state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=1000)
    score: float


class AgentWorkflowResearchResult(BaseModel):
    """Bounded Research output persisted in workflow state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: list[AgentWorkflowEvidence] = Field(default_factory=list, max_length=10)
    retrieval_count: int = Field(ge=0, le=10)
    tool_calls_used: int = Field(ge=0)
    model_id: str = Field(min_length=1, max_length=256)
    usage: TokenUsage

    @classmethod
    def from_research_result(
        cls,
        result: ResearchAgentResult,
    ) -> AgentWorkflowResearchResult:
        """Project Research output without arbitrary retrieval metadata."""
        return cls(
            evidence=[
                AgentWorkflowEvidence(
                    chunk_id=match.chunk_id,
                    document_id=match.document_id,
                    content=match.content,
                    score=match.score,
                )
                for match in result.evidence
            ],
            retrieval_count=result.retrieval_count,
            tool_calls_used=result.tool_calls_used,
            model_id=result.model_id,
            usage=result.usage,
        )

    def to_vector_matches(self) -> list[VectorMatch]:
        """Restore trusted evidence for bounded downstream agent requests."""
        return [
            VectorMatch(
                chunk_id=evidence.chunk_id,
                document_id=evidence.document_id,
                content=evidence.content,
                score=evidence.score,
            )
            for evidence in self.evidence
        ]

class AgentWorkflowStatus(StrEnum):
    """Terminal disposition of one bounded Phase 6 workflow execution."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AgentWorkflowRequest(BaseModel):
    """Trusted input to one fixed Research -> Risk -> Reviewer execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: UUID
    correlation_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("correlation_id", "question")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("workflow text fields must not be blank")
        return stripped


class AgentWorkflowResult(BaseModel):
    """Bounded terminal result for the deterministic Phase 6 workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: UUID
    correlation_id: str
    status: AgentWorkflowStatus
    failed_step: str | None = Field(default=None, max_length=64)
    research: AgentWorkflowResearchResult
    risk: RiskAgentResult
    reviewer: ReviewerAgentResult
