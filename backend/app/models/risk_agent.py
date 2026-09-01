"""Provider-neutral contracts for the bounded Risk Agent."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ai import TokenUsage, VectorMatch


class RiskEvidenceEstimate(BaseModel):
    """Non-authoritative candidate estimate grounded in trusted Research evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: str = Field(min_length=1, max_length=128)
    likelihood: float = Field(strict=True, ge=0.0, le=1.0)
    impact: float = Field(strict=True, ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=2000)

    @field_validator("likelihood", "impact")
    @classmethod
    def _finite_numeric_estimate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("risk estimates must be finite")
        return value

    @field_validator("rationale")
    @classmethod
    def _normalize_rationale(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("rationale must not be blank")
        return stripped


class RiskModelOutput(BaseModel):
    """Strict structured output accepted from the Risk Agent model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    estimates: list[RiskEvidenceEstimate] = Field(default_factory=list, max_length=10)


class RiskAgentRequest(BaseModel):
    """Trusted application input to one bounded Risk Agent execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    evidence: list[VectorMatch] = Field(max_length=10)


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
        seen: set[str] = set()
        for match in value:
            if match.chunk_id in seen:
                raise ValueError("evidence chunk_id values must be unique")
            seen.add(match.chunk_id)
        return value


class RiskAgentResult(BaseModel):
    """Validated advisory estimates produced by the bounded Risk Agent."""

    model_config = ConfigDict(frozen=True)

    estimates: list[RiskEvidenceEstimate]
    evidence_count: int = Field(ge=0)

    model_id: str
    usage: TokenUsage
