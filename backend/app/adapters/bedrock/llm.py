"""Amazon Bedrock LLM text generation adapter.

Calls Amazon Bedrock Runtime via boto3 using the Converse API.
Keeps all AWS SDK dependencies inside this adapter boundary.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import partial
from typing import Any

from app.core.errors import UpstreamError
from app.core.logging import get_logger
from app.models.ai import (
    GenerationRequest,
    GenerationResponse,
    TokenUsage,
)
from app.ports.llm_provider import LLMProvider

logger = get_logger(__name__)


class BedrockLLMProvider(LLMProvider):
    """Bedrock runtime adapter for chat/text generation using the Converse API."""

    def __init__(
        self,
        *,
        chat_model_id: str,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
        max_output_tokens: int = 2048,
        client: Any | None = None,
    ) -> None:
        if not chat_model_id:
            raise ValueError("Bedrock chat model ID is required.")

        self._chat_model_id = chat_model_id
        self._guardrail_id = guardrail_id
        self._guardrail_version = guardrail_version
        self._max_output_tokens = max_output_tokens

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
    def chat_model_id(self) -> str:
        return self._chat_model_id

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Invoke Bedrock Converse API for text completion."""
        messages: list[dict[str, Any]] = [
            {"role": msg.role, "content": [{"text": msg.content}]}
            for msg in request.messages
        ]

        inference_config: dict[str, Any] = {
            "maxTokens": request.max_tokens or self._max_output_tokens,
            "temperature": request.temperature,
        }
        if request.stop_sequences:
            inference_config["stopSequences"] = request.stop_sequences

        kwargs: dict[str, Any] = {
            "modelId": self._chat_model_id,
            "messages": messages,
            "inferenceConfig": inference_config,
        }

        if request.system_prompt:
            kwargs["system"] = [{"text": request.system_prompt}]

        if self._guardrail_id and self._guardrail_version:
            kwargs["guardrailConfig"] = {
                "guardrailIdentifier": self._guardrail_id,
                "guardrailVersion": self._guardrail_version,
            }

        start_time = datetime.now(UTC)
        try:
            response = await asyncio.to_thread(
                partial(self._client.converse, **kwargs)
            )
        except Exception as exc:
            # Safe structured error logging: do not log prompt text, messages, or raw exc string.
            logger.error(
                "bedrock_converse_call_failed",
                model_id=self._chat_model_id,
                error_type=type(exc).__name__,
            )
            raise UpstreamError("The model generation request failed.") from exc

        end_time = datetime.now(UTC)
        latency_ms = max(1, int((end_time - start_time).total_seconds() * 1000))

        # Strict response payload validation: fail closed on malformed structures
        if not isinstance(response, dict):
            logger.error(
                "bedrock_converse_invalid_response_type",
                model_id=self._chat_model_id,
            )
            raise UpstreamError("The model generation request failed.")

        stop_reason = str(response.get("stopReason") or "end_turn")
        output = response.get("output")
        if not isinstance(output, dict):
            logger.error(
                "bedrock_converse_missing_output",
                model_id=self._chat_model_id,
            )
            raise UpstreamError("The model generation request failed.")

        message_data = output.get("message")
        if not isinstance(message_data, dict):
            logger.error(
                "bedrock_converse_missing_message",
                model_id=self._chat_model_id,
            )
            raise UpstreamError("The model generation request failed.")

        content_blocks = message_data.get("content")
        if not isinstance(content_blocks, list):
            logger.error(
                "bedrock_converse_missing_content_blocks",
                model_id=self._chat_model_id,
            )
            raise UpstreamError("The model generation request failed.")

        content_parts: list[str] = []
        for block in content_blocks:
            if not isinstance(block, dict):
                logger.error(
                    "bedrock_converse_invalid_block_type",
                    model_id=self._chat_model_id,
                )
                raise UpstreamError("The model generation request failed.")
            if "text" in block:
                text_val = block["text"]
                if not isinstance(text_val, str):
                    logger.error(
                        "bedrock_converse_invalid_text_type",
                        model_id=self._chat_model_id,
                    )
                    raise UpstreamError("The model generation request failed.")
                content_parts.append(text_val)

        content_text = "".join(content_parts)
        if stop_reason == "guardrail_intervened":
            pass
        elif not content_parts or not content_text.strip():
            logger.error(
                "bedrock_converse_empty_text_content",
                model_id=self._chat_model_id,
            )
            raise UpstreamError("The model generation request failed.")

        usage_data = response.get("usage", {})
        if not isinstance(usage_data, dict):
            logger.error(
                "bedrock_converse_invalid_usage_type",
                model_id=self._chat_model_id,
            )
            raise UpstreamError("The model generation request failed.")

        try:
            input_tokens = int(usage_data.get("inputTokens", 0) or 0)
            output_tokens = int(usage_data.get("outputTokens", 0) or 0)
        except (ValueError, TypeError) as exc:
            logger.error(
                "bedrock_converse_invalid_token_counts",
                model_id=self._chat_model_id,
            )
            raise UpstreamError("The model generation request failed.") from exc

        metrics = response.get("metrics")
        if metrics is not None:
            if not isinstance(metrics, dict):
                logger.error(
                    "bedrock_converse_invalid_metrics_type",
                    model_id=self._chat_model_id,
                )
                raise UpstreamError("The model generation request failed.")

            if "latencyMs" in metrics:
                try:
                    parsed_latency = int(metrics["latencyMs"])
                    if parsed_latency < 0:
                        raise ValueError("Negative latency value")
                    latency_ms = parsed_latency
                except (ValueError, TypeError) as exc:
                    logger.error(
                        "bedrock_converse_invalid_latency_metric",
                        model_id=self._chat_model_id,
                    )
                    raise UpstreamError("The model generation request failed.") from exc

        return GenerationResponse(
            content=content_text,
            model_id=self._chat_model_id,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            stop_reason=stop_reason,
            latency_ms=latency_ms,
            generated_at=end_time,
        )
