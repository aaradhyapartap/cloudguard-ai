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


@pytest.mark.parametrize(
    "guardrail_id,guardrail_version",
    [
        ("gr-12345", None),
        (None, "1"),
        ("   ", "1"),
        ("gr-12345", "   "),
    ],
)
def test_bedrock_chat_llm_rejects_partial_guardrail_configuration(
    guardrail_id: str | None,
    guardrail_version: str | None,
) -> None:
    with pytest.raises(ValueError, match="must be configured together"):
        BedrockLLMProvider(
            chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
            guardrail_id=guardrail_id,
            guardrail_version=guardrail_version,
            client=_MockBedrockClient(),
        )


@pytest.mark.parametrize(
    "response",
    [
        {"stopReason": "guardrail_intervened"},
        {
            "stopReason": "end_turn",
            "guardrailAction": "INTERVENED",
            "output": {"message": {"content": [{"text": "blocked"}]}},
        },
    ],
)
async def test_bedrock_chat_llm_fails_closed_on_guardrail_intervention(
    response: dict[str, Any],
) -> None:
    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        guardrail_id="gr-12345",
        guardrail_version="1",
        client=_MockBedrockClient(canned_response=response),
    )

    with pytest.raises(UpstreamError, match="blocked by safety controls"):
        await provider.generate(
            GenerationRequest(messages=[Message(role="user", content="unsafe request")])
        )


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
        {
            "output": {"message": {"content": [{"text": "valid"}]}},
            "guardrailAction": {"unexpected": "shape"},
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


@pytest.mark.asyncio
async def test_bedrock_chat_llm_successful_response_with_usage_absent() -> None:
    client = _MockBedrockClient(
        canned_response={
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Response without usage."}],
                }
            },
            "stopReason": "end_turn",
        }
    )
    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=client,
    )
    req = GenerationRequest(messages=[Message(role="user", content="Test")])
    response = await provider.generate(req)

    assert response.content == "Response without usage."
    assert response.usage is None


@pytest.mark.asyncio
async def test_bedrock_chat_llm_successful_response_with_one_token_count_absent() -> None:
    # Input tokens present, output tokens absent
    client1 = _MockBedrockClient(
        canned_response={
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Response with partial usage."}],
                }
            },
            "usage": {"inputTokens": 120},
            "stopReason": "end_turn",
        }
    )
    provider1 = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=client1,
    )
    req = GenerationRequest(messages=[Message(role="user", content="Test")])
    response1 = await provider1.generate(req)

    assert response1.usage is not None
    assert response1.usage.input_tokens == 120
    assert response1.usage.output_tokens is None
    assert response1.usage.is_complete is False
    assert response1.usage.total_tokens is None

    # Output tokens present, input tokens absent
    client2 = _MockBedrockClient(
        canned_response={
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Response with partial usage."}],
                }
            },
            "usage": {"outputTokens": 45},
            "stopReason": "end_turn",
        }
    )
    provider2 = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=client2,
    )
    response2 = await provider2.generate(req)

    assert response2.usage is not None
    assert response2.usage.input_tokens is None
    assert response2.usage.output_tokens == 45
    assert response2.usage.is_complete is False
    assert response2.usage.total_tokens is None


@pytest.mark.asyncio
async def test_bedrock_chat_llm_successful_response_with_explicit_zero_counts() -> None:
    client = _MockBedrockClient(
        canned_response={
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Response with zero usage."}],
                }
            },
            "usage": {"inputTokens": 0, "outputTokens": 0},
            "stopReason": "end_turn",
        }
    )
    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=client,
    )
    req = GenerationRequest(messages=[Message(role="user", content="Test")])
    response = await provider.generate(req)

    assert response.usage is not None
    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.usage.is_complete is True
    assert response.usage.total_tokens == 0


@pytest.mark.asyncio
async def test_bedrock_chat_llm_successful_response_with_complete_counts() -> None:
    client = _MockBedrockClient(
        canned_response={
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Response with complete usage."}],
                }
            },
            "usage": {"inputTokens": 200, "outputTokens": 80},
            "stopReason": "end_turn",
        }
    )
    provider = BedrockLLMProvider(
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        client=client,
    )
    req = GenerationRequest(messages=[Message(role="user", content="Test")])
    response = await provider.generate(req)

    assert response.usage is not None
    assert response.usage.input_tokens == 200
    assert response.usage.output_tokens == 80
    assert response.usage.is_complete is True
    assert response.usage.total_tokens == 280
