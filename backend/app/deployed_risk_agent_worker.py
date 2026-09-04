"""Deployed Step Functions task for the bounded Risk Agent."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_agent_worker_settings
from app.core.container import build_risk_agent_worker_container
from app.core.logging import configure_logging
from app.models.agent_workflow import AgentWorkflowResearchResult
from app.models.principal import Principal
from app.models.risk_agent import RiskAgentRequest
from app.services.risk_agent import RiskAgent


class RiskWorkflowTaskInput(BaseModel):
    """Trusted serialized workflow state entering the Risk task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    principal: Principal
    research: AgentWorkflowResearchResult


_settings = get_agent_worker_settings()
configure_logging(_settings)
_container = build_risk_agent_worker_container(_settings)


async def _handle(event: dict[str, Any]) -> dict[str, Any]:
    task_input = RiskWorkflowTaskInput.model_validate(event)

    if not _settings.features.agentic_workflows:
        raise RuntimeError("Agentic workflows are disabled.")

    agent = RiskAgent(llm_provider=_container.llm)

    result = await agent.assess(
        principal=task_input.principal,
        request=RiskAgentRequest(
            question=task_input.question,
            evidence=task_input.research.to_vector_matches(),
        ),
    )

    return {
        "execution_id": task_input.execution_id,
        "correlation_id": task_input.correlation_id,
        "question": task_input.question,
        "principal": task_input.principal.model_dump(mode="json"),
        "research": task_input.research.model_dump(mode="json"),
        "risk": result.model_dump(mode="json"),
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entrypoint for the Risk state."""
    del context
    return asyncio.run(_handle(event))
