"""Domain models and bounded contracts for Phase 10 operational telemetry.

Enforces privacy and bounded-dimensionality invariants:
- High-cardinality identifiers (user_id, document_id, request_id, organization_id)
  and sensitive payloads (prompts, evidence, tokens, credentials, raw errors)
  are strictly prohibited as metric dimensions.
- Bounded dimension values are strictly mapped/normalized to closed safe sets.
- Correlation IDs and safe metadata are preserved in structured event records.
- Costs distinguish estimated model inference cost from actual billed provider cost.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Explicitly versioned Bedrock model pricing catalog
PRICING_CATALOG_VERSION: str = "2026-03-01"

# Bounded dimension names permitted for CloudWatch metrics
ALLOWED_METRIC_DIMENSIONS: frozenset[str] = frozenset({
    "Environment",
    "Operation",
    "ModelId",
    "Category",
    "Status",
    "Stage",
    "Severity",
    "AdmissionDecision",
})

# Prohibited dimension names (high-cardinality, tenant-identifying, or sensitive)
PROHIBITED_METRIC_DIMENSIONS: frozenset[str] = frozenset({
    "user_id",
    "document_id",
    "request_id",
    "organization_id",
    "tenant_id",
    "prompt",
    "query",
    "content",
    "evidence",
    "token",
    "task_token",
    "storage_key",
    "s3_key",
    "error_message",
    "exception",
    "secret",
    "password",
    "key",
})

SENSITIVE_CANARY_PATTERNS: tuple[str, ...] = (
    "token",
    "user",
    "doc",
    "tenant",
    "org",
    "prompt",
    "secret",
    "key",
    "password",
    "bearer",
    "sk_live",
    "akia",
)

# Closed sets of permissible dimension values
ALLOWED_ENVIRONMENTS: frozenset[str] = frozenset({"dev", "staging", "prod", "test", "local"})

ALLOWED_OPERATIONS: frozenset[str] = frozenset({
    "retrieval_search",
    "rag_generate_answer",
    "agent_workflow",
    "research_agent",
    "risk_agent",
    "reviewer_agent",
    "document_processing",
    "document_worker",
    "embedding",
    "bedrock_converse",
    "http_request",
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
    "OPTIONS",
    "OTHER",
})

ALLOWED_STATUSES: frozenset[str] = frozenset({
    "success",
    "error",
    "no_evidence",
    "succeeded",
    "failed",
    "denied",
    "allowed",
    "unknown",
})

ALLOWED_CATEGORIES: frozenset[str] = frozenset({
    "api_rate_limit",
    "worker_admission",
    "concurrency_budget",
    "ingestion_budget",
    "unauthorized_role",
    "other",
})

ALLOWED_ADMISSION_DECISIONS: frozenset[str] = frozenset({"allowed", "denied"})

ALLOWED_STAGES: frozenset[str] = frozenset({
    "workflow",
    "research",
    "risk",
    "reviewer",
    "extraction",
    "indexing",
    "other",
})

ALLOWED_SEVERITIES: frozenset[str] = frozenset({"info", "warning", "error", "critical"})

ALLOWED_MODEL_IDS: frozenset[str] = frozenset({
    "anthropic.claude-haiku-4-5-20251001-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "amazon.nova-pro-v1:0",
    "amazon.titan-embed-text-v2:0",
    "eval:fixture-llm-v1",
    "eval:feature-hash-1024-v1",
    "eval:fixture-judge-v1",
    "mock:chat-v1",
    "mock:embedding-v1",
})

ALLOWED_ADMISSION_REASONS: frozenset[str] = frozenset({
    "slot_exhausted",
    "rate_limit_exceeded",
    "token_verification_failed",
    "peer_rate_limit_exceeded",
    "authenticated_rate_limit_exceeded",
    "concurrency_limit",
    "unauthorized",
    "other",
})

# Explicit allowlists for operational safe_attributes
ALLOWED_SAFE_ATTRIBUTE_KEYS: frozenset[str] = frozenset({
    "failed_step",
    "operation",
    "stage",
    "component",
    "status",
    "reason",
    "category",
    "model_id",
    "execution_mode",
    "retrieval_count",
    "citation_count",
    "tool_calls_used",
    "is_partial",
    "is_measured",
    "error_type",
    "total_cost_usd",
    "duration_ms",
})

ALLOWED_COMPONENTS: frozenset[str] = frozenset({
    "agent_workflow",
    "document_processing",
    "retrieval",
    "rag",
    "work_admission",
    "worker",
    "bedrock_llm",
    "other",
})

ALLOWED_EXECUTION_MODES: frozenset[str] = frozenset({
    "local",
    "deployed",
    "fixture",
    "test",
    "other",
})

ALLOWED_ERROR_TYPES: frozenset[str] = frozenset({
    "UpstreamError",
    "ValueError",
    "KeyError",
    "TimeoutError",
    "RateLimitError",
    "AdmissionError",
    "SystemError",
    "ModelBudgetError",
    "Exception",
    "other",
})


def sanitize_bounded_dimension_value(dim_name: str, raw_value: Any) -> str:
    """Normalize and map dimension values to strictly bounded safe categories.

    Checks legitimate closed-set membership before canary checks so legitimate
    domain values (e.g. 'document_processing', 'token_verification_failed') are
    preserved and not scrubbed by broad canary substrings.
    """
    val_str = str(raw_value).strip()
    lowered = val_str.lower()

    match dim_name:
        case "Environment":
            if lowered in ALLOWED_ENVIRONMENTS:
                return lowered
        case "Operation":
            if val_str.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                return val_str.upper()
            if lowered in ALLOWED_OPERATIONS:
                return lowered
        case "Status":
            if lowered in ALLOWED_STATUSES:
                return lowered
            if val_str.isdigit() and len(val_str) == 3 and 100 <= int(val_str) <= 599:
                return val_str
        case "Category":
            if lowered in ALLOWED_CATEGORIES:
                return lowered
        case "AdmissionDecision":
            if lowered in ALLOWED_ADMISSION_DECISIONS:
                return lowered
        case "ModelId":
            if val_str in ALLOWED_MODEL_IDS:
                return val_str
            if lowered in {m.lower() for m in ALLOWED_MODEL_IDS}:
                return next(m for m in ALLOWED_MODEL_IDS if m.lower() == lowered)
        case "Stage":
            if lowered in ALLOWED_STAGES:
                return lowered
        case "Severity":
            if lowered in ALLOWED_SEVERITIES:
                return lowered
        case _:
            return "other"

    # If not in the legitimate closed set, check for sensitive canary patterns
    if any(p in lowered for p in SENSITIVE_CANARY_PATTERNS):
        return "other" if dim_name != "Status" else "unknown"

    if dim_name == "Status":
        return "unknown"
    if dim_name == "ModelId":
        return "unlisted_model"
    return "other"


def sanitize_admission_reason(reason: str | None) -> str | None:
    """Normalize admission denial reason to a closed safe category."""
    if reason is None:
        return None
    lowered = str(reason).strip().lower()
    if lowered in ALLOWED_ADMISSION_REASONS:
        return lowered
    if any(p in lowered for p in SENSITIVE_CANARY_PATTERNS):
        return "other"
    return "other"


class MetricUnit(StrEnum):
    MILLISECONDS = "Milliseconds"
    COUNT = "Count"
    BYTES = "Bytes"
    NONE = "None"


class AdmissionCategory(StrEnum):
    API_RATE_LIMIT = "api_rate_limit"
    WORKER_ADMISSION = "worker_admission"
    CONCURRENCY_BUDGET = "concurrency_budget"
    INGESTION_BUDGET = "ingestion_budget"
    UNAUTHORIZED_ROLE = "unauthorized_role"


class CostEstimate(BaseModel):
    """Estimated inference cost based on recorded token usage and versioned pricing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    pricing_version: str = PRICING_CATALOG_VERSION
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_inference_cost_usd: float | None = Field(default=None, ge=0.0)
    actual_billed_cost_usd: float | None = Field(default=None, ge=0.0)
    is_estimated: bool = True

    @property
    def is_measured(self) -> bool:
        return (
            self.estimated_inference_cost_usd is not None
            or self.actual_billed_cost_usd is not None
        )


class TelemetryMetricRecord(BaseModel):
    """Sanitized metric record ready for adapter dispatch or in-memory inspection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str
    value: float
    unit: MetricUnit = MetricUnit.NONE
    dimensions: dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str | None = None

    @field_validator("dimensions")
    @classmethod
    def validate_bounded_dimensions(cls, dims: dict[str, str]) -> dict[str, str]:
        validated: dict[str, str] = {}
        for k, v in dims.items():
            lowered_k = k.lower().replace("-", "_")
            if lowered_k in PROHIBITED_METRIC_DIMENSIONS or any(
                p in lowered_k for p in SENSITIVE_CANARY_PATTERNS
            ):
                raise ValueError(
                    f"Dimension '{k}' is prohibited as a metric dimension to prevent "
                    f"high cardinality and sensitive leakage."
                )
            # Find matching canonical dimension name
            canonical_name = next(
                (d for d in ALLOWED_METRIC_DIMENSIONS if d.lower() == lowered_k), None
            )
            if canonical_name is None:
                raise ValueError(f"Dimension '{k}' is not in the allowed bounded dimension set.")
            validated[canonical_name] = sanitize_bounded_dimension_value(canonical_name, v)
        return validated


class OperationalSignal(BaseModel):
    """High-level operational signal emitted by services."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_name: str
    stage: str
    status: str
    duration_ms: float | None = Field(default=None, ge=0.0)
    correlation_id: str | None = None
    safe_attributes: dict[str, Any] = Field(default_factory=dict)
    cost: CostEstimate | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("safe_attributes")
    @classmethod
    def validate_safe_attributes(cls, attrs: dict[str, Any]) -> dict[str, Any]:
        """Validate safe attributes against explicit key allowlists and closed-set values.

        Drops unknown free-text attributes; does not accept merely because they lack
        a credential keyword. Preserves legitimate closed-set values (e.g.
        'document_processing') before checking canary substrings.
        """
        sanitized: dict[str, Any] = {}
        for k, v in attrs.items():
            lowered_k = k.lower().replace("-", "_")
            if lowered_k not in ALLOWED_SAFE_ATTRIBUTE_KEYS:
                # Drop unknown attribute keys
                continue
            if any(p in lowered_k for p in SENSITIVE_CANARY_PATTERNS):
                continue

            # Safe numeric and boolean values
            if isinstance(v, bool):
                sanitized[lowered_k] = v
                continue
            if isinstance(v, (int, float)):
                if v >= 0:
                    sanitized[lowered_k] = v
                continue

            # Closed-set string attributes
            if isinstance(v, str):
                v_clean = v.strip()
                lowered_v = v_clean.lower()
                match lowered_k:
                    case "failed_step" | "stage":
                        if lowered_v in ALLOWED_STAGES:
                            sanitized[lowered_k] = lowered_v
                        elif not any(p in lowered_v for p in SENSITIVE_CANARY_PATTERNS):
                            sanitized[lowered_k] = "other"
                    case "operation":
                        if lowered_v in ALLOWED_OPERATIONS:
                            sanitized[lowered_k] = lowered_v
                        elif not any(p in lowered_v for p in SENSITIVE_CANARY_PATTERNS):
                            sanitized[lowered_k] = "other"
                    case "component":
                        if lowered_v in ALLOWED_COMPONENTS:
                            sanitized[lowered_k] = lowered_v
                        elif not any(p in lowered_v for p in SENSITIVE_CANARY_PATTERNS):
                            sanitized[lowered_k] = "other"
                    case "status":
                        if lowered_v in ALLOWED_STATUSES:
                            sanitized[lowered_k] = lowered_v
                        elif not any(p in lowered_v for p in SENSITIVE_CANARY_PATTERNS):
                            sanitized[lowered_k] = "other"
                    case "reason":
                        if lowered_v in ALLOWED_ADMISSION_REASONS:
                            sanitized[lowered_k] = lowered_v
                        elif not any(p in lowered_v for p in SENSITIVE_CANARY_PATTERNS):
                            sanitized[lowered_k] = "other"
                    case "category":
                        if lowered_v in ALLOWED_CATEGORIES:
                            sanitized[lowered_k] = lowered_v
                        elif not any(p in lowered_v for p in SENSITIVE_CANARY_PATTERNS):
                            sanitized[lowered_k] = "other"
                    case "model_id":
                        if v_clean in ALLOWED_MODEL_IDS:
                            sanitized[lowered_k] = v_clean
                        elif not any(p in lowered_v for p in SENSITIVE_CANARY_PATTERNS):
                            sanitized[lowered_k] = "unlisted_model"
                    case "execution_mode":
                        if lowered_v in ALLOWED_EXECUTION_MODES:
                            sanitized[lowered_k] = lowered_v
                        elif not any(p in lowered_v for p in SENSITIVE_CANARY_PATTERNS):
                            sanitized[lowered_k] = "other"
                    case "error_type":
                        if v_clean in ALLOWED_ERROR_TYPES:
                            sanitized[lowered_k] = v_clean
                        elif not any(p in lowered_v for p in SENSITIVE_CANARY_PATTERNS):
                            sanitized[lowered_k] = "other"
                    case _:
                        # Drop any free-text values not matching explicit closed sets
                        continue
        return sanitized
