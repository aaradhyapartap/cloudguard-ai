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
from app.security.model_budgets import (
    MAX_GENERATION_INPUT_BYTES,
    MAX_GENERATION_OUTPUT_BYTES,
    MAX_GENERATION_OUTPUT_TOKENS,
    ModelBudgetError,
    require_generation_envelope,
    require_text_budget,
)
from app.security.model_output import require_model_response_allowed

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
        max_input_bytes: int = MAX_GENERATION_INPUT_BYTES,
        client: Any | None = None,
    ) -> None:
        if not chat_model_id:
            raise ValueError("Bedrock chat model ID is required.")
        if not 1 <= max_output_tokens <= MAX_GENERATION_OUTPUT_TOKENS:
            raise ValueError("The generation output budget is invalid.")
        if not 1 <= max_input_bytes <= MAX_GENERATION_INPUT_BYTES:
            raise ValueError("The generation input budget is invalid.")
        guardrail_id = (guardrail_id.strip() or None) if guardrail_id else None
        guardrail_version = (
            (guardrail_version.strip() or None) if guardrail_version else None
        )
        if bool(guardrail_id) != bool(guardrail_version):
            raise ValueError(
                "Bedrock guardrail ID and version must be configured together."
            )

        self._chat_model_id = chat_model_id
        self._guardrail_id = guardrail_id
        self._guardrail_version = guardrail_version
        self._max_output_tokens = max_output_tokens
        self._max_input_bytes = max_input_bytes

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
                connect_timeout=3, read_timeout=60,
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        )

    @property
    def chat_model_id(self) -> str:
        return self._chat_model_id

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Invoke Bedrock Converse API for text completion."""
        try:
            # Frozen models contain mutable lists; copy/revalidate immediately
            # before dispatch so model_copy/list mutation cannot bypass limits.
            request = GenerationRequest.model_validate(
                request.model_dump(mode="json", warnings="error"),
            )
            require_generation_envelope(
                request.model_dump(mode="json"), max_bytes=self._max_input_bytes,
            )
        except (ValueError, TypeError) as exc:
            raise UpstreamError("The model request exceeds the resource budget.") from exc
        messages: list[dict[str, Any]] = [
            {"role": msg.role, "content": [{"text": msg.content}]}
            for msg in request.messages
        ]

        inference_config: dict[str, Any] = {
            "maxTokens": min(request.max_tokens, self._max_output_tokens),
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

        stop_reason = str(response.get("stopReason") or "end_turn").strip().lower()
        raw_guardrail_action = response.get("guardrailAction")
        if raw_guardrail_action is not None and not isinstance(
            raw_guardrail_action, str
        ):
            logger.error(
                "bedrock_converse_invalid_guardrail_action",
                model_id=self._chat_model_id,
            )
            raise UpstreamError("The model generation request failed.")

        guardrail_action = raw_guardrail_action or None
        if stop_reason == "guardrail_intervened" or (
            guardrail_action is not None
            and guardrail_action.strip().lower() not in {"", "none", "no_action"}
        ):
            logger.warning(
                "bedrock_guardrail_intervened",
                model_id=self._chat_model_id,
            )
            raise UpstreamError(
                "The model generation request was blocked by safety controls."
            )

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
        remaining_bytes = MAX_GENERATION_OUTPUT_BYTES
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
                try:
                    remaining_bytes -= require_text_budget(text_val, max_bytes=remaining_bytes)
                except ModelBudgetError as exc:
                    raise UpstreamError("The model response exceeds the resource budget.") from exc
                content_parts.append(text_val)

        content_text = "".join(content_parts)
        if not content_parts or not content_text.strip():
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

        result = GenerationResponse(
            content=content_text,
            model_id=self._chat_model_id,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            stop_reason=stop_reason,
            latency_ms=latency_ms,
            guardrail_action=guardrail_action,
            generated_at=end_time,
        )
        require_model_response_allowed(result)
        return result
