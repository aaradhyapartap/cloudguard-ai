"""Deployed Step Functions task for the bounded Research Agent."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_agent_worker_settings
from app.core.container import build_research_agent_worker_container
from app.core.logging import configure_logging
from app.models.agent_workflow import AgentWorkflowResearchResult
from app.models.principal import Principal
from app.models.research_agent import ResearchAgentRequest
from app.services.research_agent import ResearchAgent
from app.services.retrieval import RetrievalService
from app.services.tool_registry import ToolRegistry


class ResearchWorkflowTaskInput(BaseModel):
    """Trusted serialized workflow state entering the Research task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    principal: Principal


_settings = get_agent_worker_settings()
configure_logging(_settings)
_container = build_research_agent_worker_container(_settings)


async def _handle(event: dict[str, Any]) -> dict[str, Any]:
    task_input = ResearchWorkflowTaskInput.model_validate(event)

    if not _settings.features.agentic_workflows:
        raise RuntimeError("Agentic workflows are disabled.")

    retrieval_service = RetrievalService(
        embedding_provider=_container.embeddings,
        vector_store=_container.vectors,
    )
    tool_registry = ToolRegistry(retrieval_service=retrieval_service)
    agent = ResearchAgent(
        llm_provider=_container.llm,
        tool_registry=tool_registry,
    )

    result = await agent.research(
        principal=task_input.principal,
        request=ResearchAgentRequest(query=task_input.question),
    )
    workflow_research = AgentWorkflowResearchResult.from_research_result(result)

    return {
        "execution_id": task_input.execution_id,
        "correlation_id": task_input.correlation_id,
        "question": task_input.question,
        "principal": task_input.principal.model_dump(mode="json"),
        "research": workflow_research.model_dump(mode="json"),
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entrypoint for the Research state."""
    del context
    return asyncio.run(_handle(event))
