"""Fail-closed registry for bounded agent tool execution."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError as PydanticValidationError

from app.core.errors import AuthorizationError, ValidationError
from app.models.agents import (
    AgentType,
    SearchDocumentsArguments,
    ToolCallRequest,
    ToolCallResult,
    ToolName,
)
from app.models.principal import Principal
from app.models.retrieval import RetrievalRequest
from app.security.authz import Permission, require_permission
from app.services.retrieval import RetrievalService

_AGENT_ALLOWLISTS: dict[AgentType, frozenset[ToolName]] = {
    AgentType.RESEARCH: frozenset({ToolName.SEARCH_DOCUMENTS}),
    AgentType.RISK: frozenset(),
    AgentType.REVIEWER: frozenset(),
}


@dataclass(slots=True)
class ToolExecutionBudget:
    """Mutable budget owned by one agent/workflow execution."""

    max_calls: int = 4
    used_calls: int = 0

    def __post_init__(self) -> None:
        if self.max_calls < 1:
            raise ValueError("max_calls must be at least 1.")

    def ensure_available(self) -> None:
        if self.used_calls >= self.max_calls:
            raise ValidationError("The agent tool-call budget has been exhausted.")

    def consume(self) -> None:
        self.ensure_available()
        self.used_calls += 1


class ToolRegistry:
    """Resolve and execute agent tool intents against static allowlists."""

    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
    ) -> None:
        self._retrieval_service = retrieval_service

    async def invoke(
        self,
        *,
        agent: AgentType,
        principal: Principal,
        request: ToolCallRequest,
        budget: ToolExecutionBudget,
    ) -> ToolCallResult:
        if request.tool_name not in _AGENT_ALLOWLISTS[agent]:
            raise AuthorizationError()

        budget.consume()

        if request.tool_name is ToolName.SEARCH_DOCUMENTS:
            result = await self._search_documents(
                principal=principal,
                arguments=request.arguments,
            )
        else:
            raise ValidationError("The requested agent tool is not supported.")

        return result

    async def _search_documents(
        self,
        *,
        principal: Principal,
        arguments: dict[str, object],
    ) -> ToolCallResult:
        require_permission(principal, Permission.DOCUMENT_READ)

        try:
            parsed = SearchDocumentsArguments.model_validate(arguments)
        except PydanticValidationError as exc:
            raise ValidationError("The agent tool arguments are invalid.") from exc

        response = await self._retrieval_service.search(
            principal=principal,
            request=RetrievalRequest(
                query=parsed.query,
                top_k=parsed.top_k,
                document_ids=parsed.document_ids,
            ),
        )

        return ToolCallResult(
            tool_name=ToolName.SEARCH_DOCUMENTS,
            result=response,
        )
