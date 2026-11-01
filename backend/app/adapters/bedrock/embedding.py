"""Amazon Bedrock embedding provider adapter.

Calls Amazon Bedrock Runtime via boto3 for Amazon Titan Text Embeddings v2.
Keeps all AWS SDK dependencies inside this adapter boundary.
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any

from app.core.errors import UpstreamError
from app.core.logging import get_logger
from app.models.ai import EmbeddingResult
from app.ports.llm_provider import EmbeddingProvider
from app.ports.vector_store import validate_embedding
from app.security.model_budgets import (
    MAX_EMBEDDING_RESPONSE_BYTES,
    ModelBudgetError,
    checked_embedding_inputs,
)

logger = get_logger(__name__)


class BedrockEmbeddingProvider(EmbeddingProvider):
    """Bedrock runtime adapter for Titan Text Embeddings v2."""

    def __init__(
        self,
        *,
        embedding_model_id: str,
        dimensions: int = 1024,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not embedding_model_id:
            raise ValueError("Bedrock embedding model ID is required.")

        self._embedding_model_id = embedding_model_id
        self._dimensions = dimensions

        if client is not None:
            self._client = client
            return

        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(
                connect_timeout=3, read_timeout=30,
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        )

    @property
    def embedding_model_id(self) -> str:
        return self._embedding_model_id

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed one or more text chunks with Amazon Titan Text Embeddings v2."""
        try:
            texts = checked_embedding_inputs(texts)
        except ModelBudgetError as exc:
            raise UpstreamError("The embedding request exceeds the resource budget.") from exc
        if not texts:
            return EmbeddingResult(
                vectors=[],
                model_id=self._embedding_model_id,
                dimensions=self._dimensions,
                input_tokens=0,
            )

        vectors: list[list[float]] = []
        total_tokens = 0
        for text in texts:
            vec, tokens = await self._embed_single_text(text)
            vectors.append(vec)
            total_tokens += tokens

        return EmbeddingResult(
            vectors=vectors,
            model_id=self._embedding_model_id,
            dimensions=self._dimensions,
            input_tokens=total_tokens,
        )

    async def _embed_single_text(self, text: str) -> tuple[list[float], int]:
        """Invoke Bedrock runtime for a single text chunk with Titan Embed Text v2."""
        payload = {
            "inputText": text,
            "dimensions": self._dimensions,
            "normalize": True,
        }
        body_str = json.dumps(payload)

        try:
            response = await asyncio.to_thread(
                partial(
                    self._client.invoke_model,
                    modelId=self._embedding_model_id,
                    contentType="application/json",
                    accept="application/json",
                    body=body_str,
                )
            )
        except Exception as exc:
            # Safe structured error log: do not log payload, input text, or raw exception strings.
            logger.error(
                "bedrock_embedding_call_failed",
                model_id=self._embedding_model_id,
                error_type=type(exc).__name__,
            )
            raise UpstreamError("The document could not be embedded.") from exc

        try:
            data = await asyncio.to_thread(_decode_response_body, response.get("body"))
        except Exception as exc:
            logger.error(
                "bedrock_embedding_response_decode_failed",
                model_id=self._embedding_model_id,
                error_type=type(exc).__name__,
            )
            raise UpstreamError("The document could not be embedded.") from exc

        if not isinstance(data, dict):
            logger.error(
                "bedrock_embedding_response_not_dict",
                model_id=self._embedding_model_id,
            )
            raise UpstreamError("The document could not be embedded.")

        embedding = data.get("embedding")
        if not isinstance(embedding, list):
            logger.error(
                "bedrock_embedding_missing_vector",
                model_id=self._embedding_model_id,
            )
            raise UpstreamError("The document could not be embedded.")

        try:
            validate_embedding(embedding, label="bedrock embedding")
        except ValueError as exc:
            logger.error(
                "bedrock_embedding_invalid_vector",
                model_id=self._embedding_model_id,
                error_type=type(exc).__name__,
            )
            raise UpstreamError("The document could not be embedded.") from exc

        token_count = int(data.get("inputTextTokenCount", 0) or 0)
        return embedding, token_count


def _decode_response_body(raw_body: Any) -> Any:
    """Bound the synchronous response read and always close streaming bodies."""
    if hasattr(raw_body, "read"):
        try:
            pieces: list[bytes] = []
            received = 0
            while received <= MAX_EMBEDDING_RESPONSE_BYTES:
                piece = raw_body.read(MAX_EMBEDDING_RESPONSE_BYTES + 1 - received)
                if not piece:
                    break
                received += len(piece)
                if received > MAX_EMBEDDING_RESPONSE_BYTES:
                    raise ModelBudgetError("The embedding response exceeds the resource budget.")
                pieces.append(piece)
            return json.loads(b"".join(pieces))
        finally:
            raw_body.close()
    if isinstance(raw_body, (str, bytes)):
        size = len(raw_body.encode("utf-8")) if isinstance(raw_body, str) else len(raw_body)
        if size > MAX_EMBEDDING_RESPONSE_BYTES:
            raise ModelBudgetError("The embedding response exceeds the resource budget.")
        return json.loads(raw_body)
    if isinstance(raw_body, dict):
        if len(json.dumps(raw_body).encode("utf-8")) > MAX_EMBEDDING_RESPONSE_BYTES:
            raise ModelBudgetError("The embedding response exceeds the resource budget.")
        return raw_body
    raise ValueError("Unexpected Bedrock response body type")
