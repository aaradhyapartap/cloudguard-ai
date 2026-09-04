"""Provider-neutral contracts for the bounded Compliance Agent."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.agents import ToolName
from app.models.ai import TokenUsage
from app.models.compliance import ComplianceCandidateFinding


class ComplianceAgentRequest(BaseModel):
    """Input contract for one bounded Compliance Agent execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assessment_id: UUID
    control_ids: list[UUID] | None = Field(default=None, max_length=25)
    query_hint: str | None = Field(default=None, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10)

    @field_validator("query_hint")
    @classmethod
    def _strip_query_hint(cls, value: str | None) -> str | None:
        if value is None:
            return None

        stripped = value.strip()
        return stripped or None


class ComplianceSearchArguments(BaseModel):
    """Strict search arguments the Compliance Agent may propose."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def _normalize_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class ComplianceToolIntent(BaseModel):
    """Only model-produced tool intent accepted from the Compliance Agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: Literal[ToolName.SEARCH_DOCUMENTS]
    arguments: ComplianceSearchArguments


class ComplianceFindingProposal(BaseModel):
    """Strict model proposal before evidence provenance projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    control_id: UUID
    proposed_status: str = Field(min_length=1, max_length=64)
    rationale: str = Field(min_length=1, max_length=4000)
    evidence_sources: list[str] = Field(default_factory=list, max_length=5)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ComplianceFindingEnvelope(BaseModel):
    """Strict structured response expected from the evaluation model call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    findings: list[ComplianceFindingProposal] = Field(
        default_factory=list,
        max_length=50,
    )


class ComplianceAgentResult(BaseModel):
    """Non-authoritative output from one bounded Compliance Agent execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assessment_id: UUID
    findings: list[ComplianceCandidateFinding] = Field(
        default_factory=list,
        max_length=50,
    )
    retrieval_count: int = Field(ge=0, le=10)
    tool_calls_used: int = Field(ge=0, le=2)
    planning_model_id: str = Field(min_length=1, max_length=256)
    evaluation_model_id: str = Field(min_length=1, max_length=256)
    planning_usage: TokenUsage
    evaluation_usage: TokenUsage
