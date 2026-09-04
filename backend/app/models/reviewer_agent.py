"""Provider-neutral contracts for the bounded Reviewer Agent."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.ai import TokenUsage, VectorMatch
from app.models.risk_agent import RiskEvidenceEstimate


class ReviewDecision(StrEnum):
    """Final bounded Reviewer disposition."""

    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"


class ReviewReason(BaseModel):
    """Bounded review explanation optionally grounded in trusted evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = Field(min_length=1, max_length=1000)
    chunk_ids: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("message")
    @classmethod
    def _normalize_message(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("review reason must not be blank")
        return stripped

    @field_validator("chunk_ids")
    @classmethod
    def _validate_unique_chunk_ids(cls, value: list[str]) -> list[str]:
        if any(not chunk_id.strip() for chunk_id in value):
            raise ValueError("review reason chunk_id values must not be blank")
        if len(set(value)) != len(value):
            raise ValueError("review reason chunk_id values must be unique")
        return value


class ReviewerModelOutput(BaseModel):
    """Strict structured output accepted from the Reviewer model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ReviewDecision
    reasons: list[ReviewReason] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _fail_requires_reason(self) -> ReviewerModelOutput:
        if self.decision is ReviewDecision.FAIL and not self.reasons:
            raise ValueError("Reviewer FAIL requires at least one reason")
        return self


class ReviewerAgentRequest(BaseModel):
    """Trusted bounded workflow state supplied to one Reviewer execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    evidence: list[VectorMatch] = Field(max_length=10)
    risk_estimates: list[RiskEvidenceEstimate] = Field(max_length=10)

    @field_validator("question")
    @classmethod
    def _normalize_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped

    @field_validator("evidence")
    @classmethod
    def _validate_unique_evidence(cls, value: list[VectorMatch]) -> list[VectorMatch]:
        chunk_ids = [match.chunk_id for match in value]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("evidence chunk_id values must be unique")
        return value

    @model_validator(mode="after")
    def _risk_estimates_must_reference_trusted_evidence(self) -> ReviewerAgentRequest:
        trusted_ids = {match.chunk_id for match in self.evidence}
        estimate_ids = [estimate.chunk_id for estimate in self.risk_estimates]

        if len(set(estimate_ids)) != len(estimate_ids):
            raise ValueError("risk estimate chunk_id values must be unique")

        if any(chunk_id not in trusted_ids for chunk_id in estimate_ids):
            raise ValueError("risk estimates must reference trusted evidence")

        return self


class ReviewerAgentResult(BaseModel):
    """Validated Reviewer decision returned to workflow orchestration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ReviewDecision
    reasons: list[ReviewReason] = Field(default_factory=list, max_length=8)
    evidence_count: int = Field(ge=0, le=10)
    model_id: str = Field(min_length=1, max_length=256)
    usage: TokenUsage
