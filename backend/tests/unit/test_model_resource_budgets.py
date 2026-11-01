"""Provider-independent request limits and fail-before-dispatch regressions."""

import ast
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.adapters.bedrock.embedding import BedrockEmbeddingProvider, _decode_response_body
from app.adapters.bedrock.llm import BedrockLLMProvider
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.core.config import BedrockSettings
from app.core.errors import UpstreamError
from app.models.ai import GenerationRequest, GenerationResponse, Message, TokenUsage
from app.security.model_budgets import (
    MAX_EMBEDDING_BATCH,
    MAX_EMBEDDING_RESPONSE_BYTES,
    MAX_EMBEDDING_TEXT_BYTES,
    MAX_GENERATION_INPUT_BYTES,
    MAX_GENERATION_OUTPUT_BYTES,
    ModelBudgetError,
    checked_embedding_inputs,
    require_text_budget,
)
from app.security.model_output import require_model_response_allowed
from pydantic import ValidationError


@pytest.mark.parametrize("value", [0, -1, 4097])
def test_output_token_request_and_configuration_have_hard_ceilings(value: int) -> None:
    with pytest.raises(ValidationError):
        GenerationRequest(messages=[Message(role="user", content="hello")], max_tokens=value)
    with pytest.raises(ValidationError):
        BedrockSettings(max_output_tokens=value)


@pytest.mark.parametrize("changes", [
    {"messages": []},
    {"messages": [Message(role="user", content="x")] * 33},
    {"system_prompt": "x" * MAX_GENERATION_INPUT_BYTES},
    {"response_schema": {"description": "x" * MAX_GENERATION_INPUT_BYTES}},
    {"stop_sequences": ["stop"] * 9},
    {"stop_sequences": ["x" * 129]},
])
def test_every_generation_request_has_a_bounded_complete_envelope(changes: dict[str, Any]) -> None:
    values: dict[str, Any] = {"messages": [Message(role="user", content="hello")]}
    values.update(changes)
    with pytest.raises(ValidationError):
        GenerationRequest(**values)


def test_utf8_budget_is_bytes_not_characters() -> None:
    assert require_text_budget("éé", max_bytes=4) == 4
    with pytest.raises(ModelBudgetError):
        require_text_budget("ééé", max_bytes=4)
    with pytest.raises(ValidationError):
        GenerationRequest(messages=[Message(role="user", content="é" * 64_000)])


class RecordingChat:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "output": {"message": {"content": [{"text": "safe response"}]}},
            "usage": {"inputTokens": 1, "outputTokens": 2},
        }


async def test_configured_output_ceiling_caps_a_larger_request() -> None:
    client = RecordingChat()
    provider = BedrockLLMProvider(chat_model_id="test", max_output_tokens=64, client=client)
    await provider.generate(GenerationRequest(messages=[Message(role="user", content="hello")]))
    assert client.calls[0]["inferenceConfig"]["maxTokens"] == 64


async def test_mutated_or_copied_generation_request_cannot_bypass_dispatch_limit() -> None:
    client = RecordingChat()
    provider = BedrockLLMProvider(chat_model_id="test", client=client)
    request = GenerationRequest(messages=[Message(role="user", content="hello")])
    copied = request.model_copy(update={"max_tokens": 999_999})
    with pytest.raises(UpstreamError):
        await provider.generate(copied)
    request.messages.extend([Message(role="user", content="hello")] * 33)
    with pytest.raises(UpstreamError):
        await provider.generate(request)
    assert client.calls == []


async def test_lower_configured_input_budget_rejects_before_provider_call() -> None:
    client = RecordingChat()
    provider = BedrockLLMProvider(chat_model_id="test", max_input_bytes=100, client=client)
    with pytest.raises(UpstreamError):
        await provider.generate(
            GenerationRequest(messages=[Message(role="user", content="x" * 100)]),
        )
    assert client.calls == []


def test_oversized_model_output_fails_before_sensitive_data_parsing() -> None:
    response = GenerationResponse(
        content="x" * (MAX_GENERATION_OUTPUT_BYTES + 1), model_id="test",
        usage=TokenUsage(input_tokens=1, output_tokens=1), stop_reason="end_turn",
        latency_ms=1, generated_at=datetime.now(UTC),
    )
    with pytest.raises(UpstreamError, match="resource budget"):
        require_model_response_allowed(response)


@pytest.mark.parametrize("texts", [
    ["x"] * (MAX_EMBEDDING_BATCH + 1),
    ["safe", "x" * (MAX_EMBEDDING_TEXT_BYTES + 1)],
    ["é" * 4097],
    ["x" * 8192] * 100,
])
async def test_entire_embedding_batch_is_checked_before_first_paid_call(texts: list[str]) -> None:
    class NoCallsClient:
        def invoke_model(self, **kwargs: Any) -> None:
            raise AssertionError("No provider call is permitted")

    provider = BedrockEmbeddingProvider(embedding_model_id="test", client=NoCallsClient())
    with pytest.raises(UpstreamError, match="resource budget"):
        await provider.embed(texts)
    mock = MockEmbeddingProvider()
    with pytest.raises(ModelBudgetError):
        await mock.embed(texts)
    assert mock.embed_calls == []


def test_embedding_exact_boundaries_and_snapshot() -> None:
    assert checked_embedding_inputs(["x" * 8192]) == ["x" * 8192]
    values = ["x" * 4000] * 200
    assert checked_embedding_inputs(values) == values
    assert checked_embedding_inputs(values) is not values


@pytest.mark.parametrize("payload,oversized", [
    (b'{"embedding":[0.1]}', False),
    (b"x" * (MAX_EMBEDDING_RESPONSE_BYTES + 1), True),
], ids=["valid-response", "oversized-response"])
def test_embedding_response_reads_are_bounded_and_stream_is_closed(
    payload: bytes, oversized: bool,
) -> None:
    class BoundedStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            assert 1 <= size <= MAX_EMBEDDING_RESPONSE_BYTES + 1
            return super().read(min(size, 1024))

    stream = BoundedStream(payload)
    if oversized:
        with pytest.raises(ModelBudgetError):
            _decode_response_body(stream)
    else:
        assert _decode_response_body(stream) == {"embedding": [0.1]}
    assert stream.closed


def test_invalid_embedding_json_also_closes_stream() -> None:
    stream = io.BytesIO(b"invalid JSON")
    with pytest.raises(ValueError):
        _decode_response_body(stream)
    assert stream.closed


@pytest.mark.parametrize("filename,read_timeout", [("llm.py", 60), ("embedding.py", 30)])
def test_bedrock_sdk_attempts_and_socket_timeouts_are_explicit(
    filename: str, read_timeout: int,
) -> None:
    path = Path(__file__).resolve().parents[2] / "app" / "adapters" / "bedrock" / filename
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    configs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Config"
    ]
    assert len(configs) == 1
    values = {item.arg: ast.literal_eval(item.value) for item in configs[0].keywords}
    assert values["retries"] == {"total_max_attempts": 1, "mode": "standard"}
    assert values["connect_timeout"] == 3
    assert values["read_timeout"] == read_timeout
