"""Provider-neutral models for bounded agent tool execution."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.retrieval import RetrievalRequest, RetrievalResponse


class AgentType(StrEnum):
    RESEARCH = "research"
    RISK = "risk"
    REVIEWER = "reviewer"


class ToolName(StrEnum):
    SEARCH_DOCUMENTS = "search_documents"


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: ToolName
    result: RetrievalResponse


class SearchDocumentsArguments(RetrievalRequest):
    """Arguments accepted by the search_documents tool."""
