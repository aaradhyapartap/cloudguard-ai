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
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    @property
    def embedding_model_id(self) -> str:
        return self._embedding_model_id

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed one or more text chunks with Amazon Titan Text Embeddings v2."""
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

        raw_body = response.get("body")
        try:
            if hasattr(raw_body, "read"):
                data = json.loads(raw_body.read().decode("utf-8"))
            elif isinstance(raw_body, (str, bytes)):
                data = json.loads(raw_body)
            elif isinstance(raw_body, dict):
                data = raw_body
            else:
                raise ValueError("Unexpected Bedrock response body type")
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
