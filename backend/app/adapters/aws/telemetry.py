"""AWS CloudWatch telemetry adapter implementing Embedded Metric Format (EMF).

Emits structured logs following the AWS CloudWatch Embedded Metric Format specification:
https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html

Architectural & Operational Characteristics:
- Ingestion path: Formats EMF JSON payloads written to stdout. CloudWatch Logs
  automatically extracts metrics without custom Lambda extensions.
- In-process overhead: Writes synchronous stdout JSON lines. While avoiding external
  network I/O inside the request path, JSON formatting and I/O buffer flushing do
  incur in-process CPU and serialization overhead.
- Cost considerations: CloudWatch charges for log data ingestion and custom metric
  creation. Metric dimensions are strictly bounded to control metric explosion.
- Missing data: Operational alarms are configured with TreatMissingData.NOT_BREACHING
  to prevent false alerts during low-traffic periods.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.models.telemetry import (
    ALLOWED_METRIC_DIMENSIONS,
    PROHIBITED_METRIC_DIMENSIONS,
    SENSITIVE_CANARY_PATTERNS,
    AdmissionCategory,
    CostEstimate,
    MetricUnit,
    OperationalSignal,
    sanitize_admission_reason,
    sanitize_bounded_dimension_value,
)
from app.ports.telemetry import TelemetryEmitter
from app.telemetry.pricing import estimate_query_cost

_logger = get_logger("app.telemetry.aws")
DEFAULT_CLOUDWATCH_NAMESPACE = "CloudGuardAI"
_CANONICAL_DIMENSIONS: dict[str, str] = {d.lower(): d for d in ALLOWED_METRIC_DIMENSIONS}


def sanitize_cloudwatch_dimensions(raw_dimensions: dict[str, str]) -> dict[str, str]:
    """Filter and sanitize dimensions to strictly bounded, non-sensitive keys and safe values."""
    sanitized: dict[str, str] = {}
    for k, v in raw_dimensions.items():
        lowered_k = k.lower().replace("-", "_")
        if lowered_k in PROHIBITED_METRIC_DIMENSIONS or any(
            p in lowered_k for p in SENSITIVE_CANARY_PATTERNS
        ):
            continue

        canonical_key = _CANONICAL_DIMENSIONS.get(lowered_k)
        if canonical_key is not None:
            # Enforce bounded categorical value mapping
            safe_val = sanitize_bounded_dimension_value(canonical_key, v)
            sanitized[canonical_key] = safe_val[:250]
    return sanitized


def compute_emf_dimension_sets(clean_dims: dict[str, str]) -> list[list[str]]:
    """Generate explicit dimension sets matching dashboard and alarm queries.

    CloudWatch does not aggregate custom metrics across omitted dimensions.
    Emitting explicit subsets ensures aggregate alarms and detailed queries both resolve.
    Deduplicates sets by unordered dimension identity so no combination appears twice.
    """
    raw_sets: list[list[str]] = []
    keys = set(clean_dims.keys())

    # 1. Base Environment-only aggregation (for alarms and top-level dashboard totals)
    if "Environment" in keys:
        raw_sets.append(["Environment"])

    # 2. Operation-level aggregations
    if "Environment" in keys and "Operation" in keys:
        raw_sets.append(["Environment", "Operation"])
        if "Status" in keys:
            raw_sets.append(["Environment", "Operation", "Status"])
        if "ModelId" in keys:
            raw_sets.append(["Environment", "ModelId", "Operation"])
            if "Status" in keys:
                raw_sets.append(["Environment", "ModelId", "Operation", "Status"])

    # 3. Admission decision aggregations
    if "Environment" in keys and "Category" in keys and "AdmissionDecision" in keys:
        raw_sets.append(["Environment", "Category", "AdmissionDecision"])

    # 4. Stage aggregations
    if "Environment" in keys and "Stage" in keys:
        raw_sets.append(["Environment", "Stage"])
        if "Status" in keys:
            raw_sets.append(["Environment", "Stage", "Status"])

    # 5. Full exact key set
    if keys:
        raw_sets.append(sorted(keys))

    # Deduplicate by unordered dimension identity (frozenset)
    seen: set[frozenset[str]] = set()
    deduped: list[list[str]] = []
    for s in raw_sets:
        fs = frozenset(s)
        if fs and fs not in seen:
            seen.add(fs)
            deduped.append(sorted(s))

    return deduped if deduped else [sorted(keys)]


class CloudWatchEMFTelemetryEmitter(TelemetryEmitter):
    """CloudWatch Embedded Metric Format (EMF) Telemetry Emitter."""

    def __init__(
        self,
        *,
        namespace: str = DEFAULT_CLOUDWATCH_NAMESPACE,
        environment: str = "dev",
    ) -> None:
        self._namespace = namespace
        # Normalize environment to allowed bounded values
        self._environment = sanitize_bounded_dimension_value("Environment", environment)

    def _emit_emf(
        self,
        *,
        metric_name: str,
        value: float,
        unit: MetricUnit,
        dimensions: dict[str, str],
        correlation_id: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        """Format and write an EMF payload to stdout."""
        try:
            clean_dims = sanitize_cloudwatch_dimensions(dimensions)
            clean_dims["Environment"] = self._environment

            dimension_sets = compute_emf_dimension_sets(clean_dims)
            timestamp_ms = int(datetime.now(UTC).timestamp() * 1000)

            emf_payload: dict[str, Any] = {
                "_aws": {
                    "Timestamp": timestamp_ms,
                    "CloudWatchMetrics": [
                        {
                            "Namespace": self._namespace,
                            "Dimensions": dimension_sets,
                            "Metrics": [
                                {
                                    "Name": metric_name,
                                    "Unit": unit.value,
                                }
                            ],
                        }
                    ],
                },
                metric_name: value,
                **clean_dims,
            }

            if correlation_id:
                clean_corr = str(correlation_id)[:128]
                if not any(p in clean_corr.lower() for p in SENSITIVE_CANARY_PATTERNS):
                    emf_payload["correlation_id"] = clean_corr

            if extra_fields:
                for k, v in extra_fields.items():
                    lowered_k = k.lower().replace("-", "_")
                    if any(p in lowered_k for p in SENSITIVE_CANARY_PATTERNS):
                        continue
                    if isinstance(v, (int, float, bool)):
                        emf_payload[k] = v
                    elif isinstance(v, str):
                        v_str = str(v)[:128]
                        if not any(p in v_str.lower() for p in SENSITIVE_CANARY_PATTERNS):
                            emf_payload[k] = v_str

            sys.stdout.write(json.dumps(emf_payload) + "\n")
            sys.stdout.flush()
        except Exception:
            _logger.warning("aws_emf_telemetry_emission_suppressed")

    def record_latency(
        self,
        operation: str,
        duration_ms: float,
        *,
        dimensions: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        dims = dict(dimensions or {})
        dims.setdefault("Operation", operation)
        self._emit_emf(
            metric_name="OperationDurationMs",
            value=round(max(0.0, duration_ms), 3),
            unit=MetricUnit.MILLISECONDS,
            dimensions=dims,
            correlation_id=correlation_id,
        )

    def record_counter(
        self,
        metric_name: str,
        value: float = 1.0,
        *,
        unit: MetricUnit = MetricUnit.COUNT,
        dimensions: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        dims = dict(dimensions or {})
        self._emit_emf(
            metric_name=metric_name,
            value=value,
            unit=unit,
            dimensions=dims,
            correlation_id=correlation_id,
        )

    def record_token_usage(
        self,
        *,
        model_id: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        operation: str = "inference",
        correlation_id: str | None = None,
        actual_billed_usd: float | None = None,
    ) -> CostEstimate:
        cost = estimate_query_cost(
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            actual_billed_usd=actual_billed_usd,
        )
        dims = {"ModelId": model_id, "Operation": operation}
        if input_tokens is not None:
            self.record_counter(
                "InputTokens",
                float(input_tokens),
                unit=MetricUnit.COUNT,
                dimensions=dims,
                correlation_id=correlation_id,
            )
        if output_tokens is not None:
            self.record_counter(
                "OutputTokens",
                float(output_tokens),
                unit=MetricUnit.COUNT,
                dimensions=dims,
                correlation_id=correlation_id,
            )
        if cost.estimated_inference_cost_usd is not None:
            self.record_counter(
                "ModelCallEstimatedCostUSD",
                cost.estimated_inference_cost_usd,
                unit=MetricUnit.NONE,
                dimensions=dims,
                correlation_id=correlation_id,
            )
            self.record_counter(
                "CostMeasuredModelCalls",
                1.0,
                unit=MetricUnit.COUNT,
                dimensions=dims,
                correlation_id=correlation_id,
            )
        else:
            self.record_counter(
                "CostUnmeasuredModelCalls",
                1.0,
                unit=MetricUnit.COUNT,
                dimensions=dims,
                correlation_id=correlation_id,
            )
        return cost

    def record_query_cost(
        self,
        *,
        total_cost_usd: float | None,
        is_measured: bool,
        is_partial: bool = False,
        operation: str = "rag_generate_answer",
        correlation_id: str | None = None,
    ) -> None:
        """Record query-level cost and pricing coverage once at the user query boundary."""
        dims = {"Operation": operation}
        if is_measured and not is_partial and total_cost_usd is not None:
            self.record_counter(
                "EstimatedInferenceCostUSD",
                total_cost_usd,
                unit=MetricUnit.NONE,
                dimensions=dims,
                correlation_id=correlation_id,
            )
            self.record_counter(
                "CostMeasuredQueries",
                1.0,
                unit=MetricUnit.COUNT,
                dimensions=dims,
                correlation_id=correlation_id,
            )
        else:
            self.record_counter(
                "CostUnmeasuredQueries",
                1.0,
                unit=MetricUnit.COUNT,
                dimensions=dims,
                correlation_id=correlation_id,
            )
            if is_partial:
                self.record_counter(
                    "CostPartialQueries",
                    1.0,
                    unit=MetricUnit.COUNT,
                    dimensions=dims,
                    correlation_id=correlation_id,
                )

    def record_admission_decision(
        self,
        *,
        category: AdmissionCategory | str,
        decision: str,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        cat_str = category.value if isinstance(category, AdmissionCategory) else str(category)
        clean_reason = sanitize_admission_reason(reason)
        metric_name = "AdmissionAllowed" if decision == "allowed" else "AdmissionDenied"
        self._emit_emf(
            metric_name=metric_name,
            value=1.0,
            unit=MetricUnit.COUNT,
            dimensions={"Category": cat_str, "AdmissionDecision": decision},
            correlation_id=correlation_id,
            extra_fields={"safe_reason": clean_reason} if clean_reason else None,
        )

    def record_operational_signal(self, signal: OperationalSignal) -> None:
        dims = {"Stage": signal.stage, "Status": signal.status}
        if signal.duration_ms is not None:
            self._emit_emf(
                metric_name="StageDurationMs",
                value=signal.duration_ms,
                unit=MetricUnit.MILLISECONDS,
                dimensions=dims,
                correlation_id=signal.correlation_id,
            )
        self.record_counter(
            "StageInvocations",
            1.0,
            unit=MetricUnit.COUNT,
            dimensions=dims,
            correlation_id=signal.correlation_id,
        )
