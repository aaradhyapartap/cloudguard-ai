"""Unit tests for BedrockLLMProvider chat generation adapter."""

from __future__ import annotations

from typing import Any

import pytest
from app.adapters.bedrock.llm import BedrockLLMProvider
from app.core.errors import UpstreamError
from app.models.ai import GenerationRequest, Message


class _MockBedrockClient:
    def __init__(self, canned_response: dict[str, Any] | Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        if canned_response is None:
            self.canned_response: dict[str, Any] | Exception = {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [{"text": "Based on [S1], access controls are enforced."}],
                    }
                },
                "usage": {"inputTokens": 120, "outputTokens": 45},
                "stopReason": "end_turn",
                "metrics": {"latencyMs": 250},
            }
        else:
            self.canned_response = canned_response

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if isinstance(self.canned_response, Exception):
            raise self.canned_response
        return self.canned_response


async def test_bedrock_chat_llm_success() -> None:
    client = _MockBedrockClient()
    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        region="us-east-1",
        client=client,
    )

    request = GenerationRequest(
        messages=[Message(role="user", content="Explain policy AC-2")],
        system_prompt="You are a security assistant.",
        temperature=0.0,
        max_tokens=1024,
    )

    response = await provider.generate(request)

    assert response.content == "Based on [S1], access controls are enforced."
    assert response.model_id == "anthropic.claude-haiku-4-5-20251001-v1:0"
    assert response.usage.input_tokens == 120
    assert response.usage.output_tokens == 45
    assert response.stop_reason == "end_turn"
    assert response.latency_ms == 250

    # Verify converse call kwargs
    assert len(client.calls) == 1
    call_kwargs = client.calls[0]
    assert call_kwargs["modelId"] == "anthropic.claude-haiku-4-5-20251001-v1:0"
    assert call_kwargs["system"] == [{"text": "You are a security assistant."}]
    assert call_kwargs["messages"] == [
        {"role": "user", "content": [{"text": "Explain policy AC-2"}]}
    ]
    assert call_kwargs["inferenceConfig"]["maxTokens"] == 1024
    assert call_kwargs["inferenceConfig"]["temperature"] == 0.0


async def test_bedrock_chat_llm_with_guardrail_config() -> None:
    client = _MockBedrockClient()
    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        region="us-east-1",
        guardrail_id="gr-12345",
        guardrail_version="1",
        client=client,
    )

    request = GenerationRequest(
        messages=[Message(role="user", content="Hello")],
    )

    await provider.generate(request)

    call_kwargs = client.calls[0]
    assert call_kwargs["guardrailConfig"] == {
        "guardrailIdentifier": "gr-12345",
        "guardrailVersion": "1",
    }


async def test_bedrock_chat_llm_handles_client_exception() -> None:
    client = _MockBedrockClient(
        canned_response=RuntimeError("Bedrock throttling exception: Rate limit exceeded")
    )
    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=client,
    )

    request = GenerationRequest(
        messages=[Message(role="user", content="Test question")],
    )

    with pytest.raises(UpstreamError, match=r"The model generation request failed\."):
        await provider.generate(request)


@pytest.mark.parametrize(
    "malformed_payload",
    [
        {"invalid": "payload"},
        {"output": {}},
        {"output": {"message": {}}},
        {"output": {"message": {"content": []}}},
        {"output": {"message": {"content": [{"text": 123}]}}},
        {"output": {"message": {"content": [{"text": "   "}]}}},
        {
            "output": {"message": {"content": [{"text": "valid"}]}},
            "usage": {"inputTokens": "not-an-int"},
        },
        {
            "output": {"message": {"content": [{"text": "valid"}]}},
            "metrics": "not-a-dict",
        },
        {
            "output": {"message": {"content": [{"text": "valid"}]}},
            "metrics": {"latencyMs": "not-an-int"},
        },
        {
            "output": {"message": {"content": [{"text": "valid"}]}},
            "metrics": {"latencyMs": -10},
        },
    ],
)
async def test_bedrock_chat_llm_fails_closed_on_malformed_response(
    malformed_payload: dict[str, Any],
) -> None:
    client = _MockBedrockClient(canned_response=malformed_payload)
    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=client,
    )

    request = GenerationRequest(
        messages=[Message(role="user", content="Test question")],
    )

    with pytest.raises(UpstreamError, match=r"The model generation request failed\."):
        await provider.generate(request)


async def test_bedrock_chat_llm_absent_metrics_succeeds_with_measured_latency() -> None:
    client = _MockBedrockClient(
        canned_response={
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Valid response without metrics."}],
                }
            },
            "usage": {"inputTokens": 100, "outputTokens": 20},
            "stopReason": "end_turn",
        }
    )
    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=client,
    )

    request = GenerationRequest(
        messages=[Message(role="user", content="Test question")],
    )

    response = await provider.generate(request)
    assert response.content == "Valid response without metrics."
    assert response.latency_ms >= 1


async def test_bedrock_chat_llm_valid_latency_metric_succeeds() -> None:
    client = _MockBedrockClient(
        canned_response={
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Valid response with metrics."}],
                }
            },
            "usage": {"inputTokens": 100, "outputTokens": 20},
            "stopReason": "end_turn",
            "metrics": {"latencyMs": 420},
        }
    )
    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=client,
    )

    request = GenerationRequest(
        messages=[Message(role="user", content="Test question")],
    )

    response = await provider.generate(request)
    assert response.content == "Valid response with metrics."
    assert response.latency_ms == 420


def test_bedrock_chat_llm_requires_model_id() -> None:
    with pytest.raises(ValueError, match=r"Bedrock chat model ID is required\."):
        BedrockLLMProvider(chat_model_id="")
