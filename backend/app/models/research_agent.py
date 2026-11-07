"""Provider-neutral contracts for the bounded Research Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.agents import ToolName
from app.models.ai import TokenUsage, VectorMatch


class ResearchAgentRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=2000)

    @field_validator("query")
    @classmethod
    def _normalize_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class ResearchSearchArguments(BaseModel):
    """Strict arguments the Research Agent may propose for document search."""

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


class ResearchToolIntent(BaseModel):
    """Only model-produced tool intent accepted from the Research Agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: Literal[ToolName.SEARCH_DOCUMENTS]
    arguments: ResearchSearchArguments


class ResearchAgentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence: list[VectorMatch]
    retrieval_count: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    model_id: str
    usage: TokenUsage
