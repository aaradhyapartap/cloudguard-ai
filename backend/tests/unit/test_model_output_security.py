"""Provider-neutral model output safety checks."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.errors import UpstreamError
from app.models.ai import GenerationResponse, TokenUsage
from app.security.model_output import require_model_response_allowed


def _response(
    *,
    stop_reason: str = "end_turn",
    guardrail_action: str | None = None,
) -> GenerationResponse:
    return GenerationResponse(
        content="safe",
        model_id="mock:test",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        stop_reason=stop_reason,
        latency_ms=1,
        guardrail_action=guardrail_action,
        generated_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "response",
    [
        _response(stop_reason="guardrail_intervened"),
        _response(guardrail_action="INTERVENED"),
        _response(guardrail_action="BLOCKED"),
    ],
)
def test_model_safety_intervention_fails_closed(response: GenerationResponse) -> None:
    with pytest.raises(UpstreamError, match="blocked by safety controls"):
        require_model_response_allowed(response)


@pytest.mark.parametrize("action", [None, "", "NONE", "no_action"])
def test_no_intervention_is_accepted(action: str | None) -> None:
    require_model_response_allowed(_response(guardrail_action=action))
