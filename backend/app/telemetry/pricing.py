"""Explicitly versioned pricing catalog and inference cost estimation.

Rules:
- Pricing is strictly versioned, verified against official AWS Bedrock sources, and documented.
- Cost estimates are computed only when model token usage is known.
- Distinguishes estimated inference cost from actual billed provider cost.
- Unknown, mock, or offline fixture models return None ("Unknown costs remain unmeasured").
- Never fabricates or estimates billing when rates are unlisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.models.telemetry import PRICING_CATALOG_VERSION, CostEstimate

# Source: AWS Bedrock Pricing (https://aws.amazon.com/bedrock/pricing/)
# Verification Date: 2026-03-01
# Region: us-east-1 (US East, N. Virginia)
# Inference Tier: On-Demand
PRICING_VERIFICATION_DATE: Final[str] = "2026-03-01"
PRICING_REGION: Final[str] = "us-east-1"
PRICING_INFERENCE_TIER: Final[str] = "On-Demand"
PRICING_SOURCE_URL: Final[str] = "https://aws.amazon.com/bedrock/pricing/"


@dataclass(frozen=True)
class ModelPricingRate:
    """Per-token pricing rates in USD verified against official AWS documentation."""

    input_cost_per_thousand: float
    output_cost_per_thousand: float
    region: str = PRICING_REGION
    inference_tier: str = PRICING_INFERENCE_TIER
    verification_date: str = PRICING_VERIFICATION_DATE
    source_url: str = PRICING_SOURCE_URL


# Version 2026-03-01 AWS Bedrock public rates (us-east-1, On-Demand)
_BEDROCK_PRICING_CATALOG: Final[dict[str, ModelPricingRate]] = {
    # Anthropic Claude 3.5 Haiku ($1.00 / 1M input, $5.00 / 1M output)
    "anthropic.claude-haiku-4-5-20251001-v1:0": ModelPricingRate(
        input_cost_per_thousand=0.001,
        output_cost_per_thousand=0.005,
    ),
    # Anthropic Claude 3 Haiku ($0.25 / 1M input, $1.25 / 1M output)
    "anthropic.claude-3-haiku-20240307-v1:0": ModelPricingRate(
        input_cost_per_thousand=0.00025,
        output_cost_per_thousand=0.00125,
    ),
    # Amazon Nova Pro ($0.80 / 1M input, $3.20 / 1M output)
    "amazon.nova-pro-v1:0": ModelPricingRate(
        input_cost_per_thousand=0.0008,
        output_cost_per_thousand=0.0032,
    ),
    # Amazon Titan Text Embeddings V2 ($0.02 / 1M input, no output tokens)
    "amazon.titan-embed-text-v2:0": ModelPricingRate(
        input_cost_per_thousand=0.00002,
        output_cost_per_thousand=0.0,
    ),
}


def get_model_pricing_rate(
    model_id: str,
    pricing_version: str = PRICING_CATALOG_VERSION,
) -> ModelPricingRate | None:
    """Retrieve pricing rate for a known model. Returns None if unmeasured or unknown."""
    if pricing_version != PRICING_CATALOG_VERSION:
        return None
    return _BEDROCK_PRICING_CATALOG.get(model_id)


def estimate_query_cost(
    *,
    model_id: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    actual_billed_usd: float | None = None,
    pricing_version: str = PRICING_CATALOG_VERSION,
) -> CostEstimate:
    """Calculate inference cost estimate based on recorded token counts.

    If token counts are absent or incomplete (either input_tokens or output_tokens
    is None), or if the model is unlisted, estimated cost is None ("unmeasured").
    Known zero usage (input_tokens == 0 and output_tokens == 0) for recognized models
    results in a measured cost of $0.0.
    """
    rate = get_model_pricing_rate(model_id, pricing_version)

    if input_tokens is None or output_tokens is None:
        total_tokens: int | None = None
        estimated_usd: float | None = None
    else:
        total_tokens = max(0, input_tokens) + max(0, output_tokens)
        estimated_usd = None
        if rate is not None:
            in_cost = (max(0, input_tokens) / 1000.0) * rate.input_cost_per_thousand
            out_cost = (max(0, output_tokens) / 1000.0) * rate.output_cost_per_thousand
            estimated_usd = round(in_cost + out_cost, 7)

    return CostEstimate(
        model_id=model_id,
        pricing_version=pricing_version,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_inference_cost_usd=estimated_usd,
        actual_billed_cost_usd=actual_billed_usd,
        is_estimated=True,
    )
