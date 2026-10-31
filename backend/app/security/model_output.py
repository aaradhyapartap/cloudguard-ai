"""Provider-neutral fail-closed checks for model generation responses."""

from __future__ import annotations

from app.core.errors import UpstreamError
from app.models.ai import GenerationResponse

_NO_INTERVENTION_ACTIONS = frozenset({"", "none", "no_action"})


def require_model_response_allowed(response: GenerationResponse) -> None:
    """Reject any provider response that reports a safety intervention."""
    stop_reason = response.stop_reason.strip().lower()
    guardrail_action = (response.guardrail_action or "").strip().lower()

    if (
        stop_reason == "guardrail_intervened"
        or guardrail_action not in _NO_INTERVENTION_ACTIONS
    ):
        raise UpstreamError("The model generation request was blocked by safety controls.")
