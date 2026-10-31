"""Provider-neutral fail-closed checks for model generation responses."""

from __future__ import annotations

import json

from app.core.errors import UpstreamError
from app.models.ai import GenerationResponse
from app.security.sensitive_data import DeterministicSensitiveDataDetector

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

    detector = DeterministicSensitiveDataDetector()
    candidates = [response.content]
    try:
        # Scan decoded JSON too, so unicode-escaped credentials/PII cannot bypass
        # the check before structured model output is consumed by an agent.
        candidates.append(json.dumps(json.loads(response.content), ensure_ascii=False))
    except (ValueError, RecursionError):
        # Structured-output parsers still reject malformed JSON; plain RAG prose
        # is scanned directly regardless of whether it is JSON.
        pass
    if any(detector.detect(candidate) for candidate in candidates):
        raise UpstreamError("The model generation request was blocked by sensitive-data controls.")
