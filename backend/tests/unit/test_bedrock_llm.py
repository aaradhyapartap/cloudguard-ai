"""Unit tests for BedrockEmbeddingProvider and MockEmbeddingProvider."""

from __future__ import annotations

import io
import json
import re
from typing import Any

import pytest
from app.adapters.bedrock.embedding import BedrockEmbeddingProvider
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.core.errors import UpstreamError
from app.models.ai import EmbeddingResult


class FakeBedrockRuntimeClient:
    """In-memory mock for Bedrock Runtime client."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.should_fail: bool = False
        self.response_body: dict[str, Any] | None = None
        self.raw_body_bytes: bytes | None = None

    def invoke_model(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.should_fail:
            raise RuntimeError("Bedrock service throttled / unreachable")

        if self.raw_body_bytes is not None:
            return {"body": io.BytesIO(self.raw_body_bytes)}

        if self.response_body is not None:
            body_bytes = json.dumps(self.response_body).encode("utf-8")
            return {"body": io.BytesIO(body_bytes)}

        # Default valid Titan Embed Text v2 response (1024 floats)
        default_resp = {
            "embedding": [0.01] * 1024,
            "inputTextTokenCount": 15,
        }
        return {"body": io.BytesIO(json.dumps(default_resp).encode("utf-8"))}


def test_mock_embedding_provider_deterministic_output() -> None:
    provider = MockEmbeddingProvider(dimensions=1024)
    assert provider.embedding_model_id == "mock:embed-v1"


@pytest.mark.asyncio
async def test_mock_embedding_provider_embed_produces_1024_dim_vectors() -> None:
    provider = MockEmbeddingProvider(dimensions=1024)
    result = await provider.embed(["First policy chunk", "Second policy chunk"])

    assert isinstance(result, EmbeddingResult)
    assert len(result.vectors) == 2
    assert len(result.vectors[0]) == 1024
    assert len(result.vectors[1]) == 1024
    assert result.dimensions == 1024
    assert result.input_tokens > 0

    # Determinism: same input yields same vector
    result2 = await provider.embed(["First policy chunk"])
    assert result2.vectors[0] == result.vectors[0]

    # Distinguishability: different input yields different vector
    assert result.vectors[0] != result.vectors[1]


@pytest.mark.asyncio
async def test_bedrock_embed_success_without_chat_model() -> None:
    """BedrockEmbeddingProvider initializes with only embedding configuration."""
    fake_client = FakeBedrockRuntimeClient()
    provider = BedrockEmbeddingProvider(
        embedding_model_id="amazon.titan-embed-text-v2:0",
        dimensions=1024,
        client=fake_client,
    )

    texts = ["Cloud security overview", "Tenant data protection"]
    result = await provider.embed(texts)

    assert len(result.vectors) == 2
    assert len(result.vectors[0]) == 1024
    assert result.dimensions == 1024
    assert result.model_id == "amazon.titan-embed-text-v2:0"
    assert result.input_tokens == 30  # 15 * 2
    assert len(fake_client.calls) == 2

    # Check request payload sent to Bedrock
    call_body = json.loads(fake_client.calls[0]["body"])
    assert call_body["inputText"] == "Cloud security overview"
    assert call_body["dimensions"] == 1024
    assert call_body["normalize"] is True


@pytest.mark.asyncio
async def test_bedrock_embed_empty_texts_returns_empty_result() -> None:
    fake_client = FakeBedrockRuntimeClient()
    provider = BedrockEmbeddingProvider(
        embedding_model_id="amazon.titan-embed-text-v2:0",
        client=fake_client,
    )

    result = await provider.embed([])
    assert result.vectors == []
    assert result.input_tokens == 0
    assert len(fake_client.calls) == 0


@pytest.mark.asyncio
async def test_bedrock_embed_provider_failure_raises_upstream_error() -> None:
    fake_client = FakeBedrockRuntimeClient()
    fake_client.should_fail = True

    provider = BedrockEmbeddingProvider(
        embedding_model_id="amazon.titan-embed-text-v2:0",
        client=fake_client,
    )

    with pytest.raises(UpstreamError, match=re.escape("The document could not be embedded.")):
        await provider.embed(["Sample document text"])


@pytest.mark.asyncio
async def test_bedrock_embed_malformed_json_raises_upstream_error() -> None:
    fake_client = FakeBedrockRuntimeClient()
    fake_client.raw_body_bytes = b"not valid json {"

    provider = BedrockEmbeddingProvider(
        embedding_model_id="amazon.titan-embed-text-v2:0",
        client=fake_client,
    )

    with pytest.raises(UpstreamError, match=re.escape("The document could not be embedded.")):
        await provider.embed(["Sample document text"])


@pytest.mark.asyncio
async def test_bedrock_embed_missing_embedding_key_raises_upstream_error() -> None:
    fake_client = FakeBedrockRuntimeClient()
    fake_client.response_body = {"inputTextTokenCount": 10}  # missing "embedding"

    provider = BedrockEmbeddingProvider(
        embedding_model_id="amazon.titan-embed-text-v2:0",
        client=fake_client,
    )

    with pytest.raises(UpstreamError, match=re.escape("The document could not be embedded.")):
        await provider.embed(["Sample document text"])


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_vec", [
    [0.1] * 512,
    [0.1] * 1025,
    [float("nan")] * 1024,
    [float("inf")] * 1024,
])
async def test_bedrock_embed_invalid_vector_values_raises_upstream_error(
    invalid_vec: list[float],
) -> None:
    fake_client = FakeBedrockRuntimeClient()
    fake_client.response_body = {
        "embedding": invalid_vec,
        "inputTextTokenCount": 10,
    }

    provider = BedrockEmbeddingProvider(
        embedding_model_id="amazon.titan-embed-text-v2:0",
        client=fake_client,
    )

    with pytest.raises(UpstreamError, match=re.escape("The document could not be embedded.")):
        await provider.embed(["Sample document text"])
