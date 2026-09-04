"""Provider-neutral models for bounded agent tool execution."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.compliance import ComplianceControlRead, ComplianceFrameworkRead
from app.models.retrieval import RetrievalRequest, RetrievalResponse


class AgentType(StrEnum):
    RESEARCH = "research"
    COMPLIANCE = "compliance"
    RISK = "risk"
    REVIEWER = "reviewer"


class ToolName(StrEnum):
    SEARCH_DOCUMENTS = "search_documents"
    GET_POLICY = "get_policy"


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


class PolicyReadResult(BaseModel):
    """Read-only compliance policy/control context returned to an agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assessment_id: UUID
    framework: ComplianceFrameworkRead
    controls: list[ComplianceControlRead] = Field(max_length=25)


class ToolCallResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: ToolName
    result: RetrievalResponse | PolicyReadResult


class SearchDocumentsArguments(RetrievalRequest):
    """Arguments accepted by the search_documents tool."""


class GetPolicyArguments(BaseModel):
    """Arguments accepted by the read-only get_policy tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assessment_id: UUID
    control_ids: list[UUID] | None = Field(default=None, max_length=25)
