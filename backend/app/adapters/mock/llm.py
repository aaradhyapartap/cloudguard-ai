"""In-memory LLM provider for local development and unit tests.

Deterministic by construction: the same request always yields the same
response, keyed on a hash of the request. That determinism is the entire point —
a test that sometimes passes is worse than no test.

This is not the ``recorded`` provider. That one (Phase 4) replays real Bedrock
responses from cassettes. This one fabricates plausible shapes so the pipeline
can be exercised end to end with no network at all.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from app.adapters.mock.embedding import MockEmbeddingProvider
from app.models.ai import (
    GenerationRequest,
    GenerationResponse,
    TokenUsage,
)
from app.ports.llm_provider import LLMProvider

_MOCK_CHAT_MODEL = "mock:chat-v1"

__all__ = ["MockEmbeddingProvider", "MockLLMProvider"]


def _stable_hash(payload: str) -> int:
    return int(hashlib.sha256(payload.encode()).hexdigest()[:16], 16)


def _approx_tokens(text: str) -> int:
    """~4 characters per token. Good enough for a test double; never for billing."""
    return max(1, len(text) // 4)


class MockLLMProvider(LLMProvider):
    def __init__(self) -> None:
        self.generate_calls: list[GenerationRequest] = []

    @property
    def chat_model_id(self) -> str:
        return _MOCK_CHAT_MODEL

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.generate_calls.append(request)
        prompt = "\n".join(message.content for message in request.messages)

        if request.response_schema is not None:
            content = json.dumps(_schema_stub(request.response_schema), sort_keys=True)
        else:
            content = (
                "[mock] No model was called. Set LLM_PROVIDER=bedrock for real "
                f"inference. Prompt digest: {_stable_hash(prompt) % 10**8:08d}"
            )

        return GenerationResponse(
            content=content,
            model_id=_MOCK_CHAT_MODEL,
            usage=TokenUsage(
                input_tokens=_approx_tokens(prompt),
                output_tokens=_approx_tokens(content),
            ),
            stop_reason="end_turn",
            latency_ms=1,
            generated_at=datetime.now(UTC),
        )


def _schema_stub(schema: dict[str, object]) -> dict[str, object]:
    """Build a deterministic minimal value satisfying the supplied JSON schema."""
    value = _schema_value(schema, schema)
    return value if isinstance(value, dict) else {}


def _schema_value(
    definition: dict[str, object],
    root_schema: dict[str, object],
) -> object:
    ref = definition.get("$ref")
    if isinstance(ref, str):
        resolved = _resolve_local_ref(ref, root_schema)
        if resolved is not None:
            return _schema_value(resolved, root_schema)

    if "const" in definition:
        return definition["const"]

    enum = definition.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]

    if "default" in definition:
        return definition["default"]

    kind = definition.get("type")

    if kind == "object":
        properties = definition.get("properties")
        if not isinstance(properties, dict):
            return {}

        required = definition.get("required")
        required_names = set(required) if isinstance(required, list) else set()

        result: dict[str, object] = {}
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, dict):
                continue
            if name in required_names or "default" in child:
                result[name] = _schema_value(child, root_schema)
        return result

    if kind == "array":
        return []

    if kind == "string":
        min_length = definition.get("minLength")
        if isinstance(min_length, int) and min_length > 0:
            return "m" * min_length
        return ""

    if kind == "integer":
        minimum = definition.get("minimum")
        return minimum if isinstance(minimum, int) else 0

    if kind == "number":
        minimum = definition.get("minimum")
        return float(minimum) if isinstance(minimum, (int, float)) else 0.0

    if kind == "boolean":
        return False

    return None


def _resolve_local_ref(
    ref: str,
    root_schema: dict[str, object],
) -> dict[str, object] | None:
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        return None

    definitions = root_schema.get("$defs")
    if not isinstance(definitions, dict):
        return None

    resolved = definitions.get(ref[len(prefix) :])
    return resolved if isinstance(resolved, dict) else None
