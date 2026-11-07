"""In-memory operational telemetry emitter for local development and deterministic tests."""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from app.core.logging import get_logger
from app.models.telemetry import (
    AdmissionCategory,
    CostEstimate,
    MetricUnit,
    OperationalSignal,
    TelemetryMetricRecord,
    sanitize_admission_reason,
)
from app.ports.telemetry import TelemetryEmitter
from app.telemetry.pricing import estimate_query_cost

_logger = get_logger("app.telemetry")
DEFAULT_MAX_RECORDS = 10_000


class InMemoryTelemetryEmitter(TelemetryEmitter):
    """Stores emitted telemetry in memory with bounded retention and structured logging."""

    def __init__(self, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self._max_records = max(1, max_records)
        self.metrics: deque[TelemetryMetricRecord] = deque(maxlen=self._max_records)
        self.costs: deque[CostEstimate] = deque(maxlen=self._max_records)
        self.signals: deque[OperationalSignal] = deque(maxlen=self._max_records)
        self.admission_decisions: deque[dict[str, Any]] = deque(maxlen=self._max_records)

    def clear(self) -> None:
        """Reset all in-memory records (useful between tests)."""
        self.metrics.clear()
        self.costs.clear()
        self.signals.clear()
        self.admission_decisions.clear()

    def record_latency(
        self,
        operation: str,
        duration_ms: float,
        *,
        dimensions: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        try:
            dims = dict(dimensions or {})
            dims.setdefault("Operation", operation)
            record = TelemetryMetricRecord(
                metric_name="OperationDurationMs",
                value=round(max(0.0, duration_ms), 3),
                unit=MetricUnit.MILLISECONDS,
                dimensions=dims,
                correlation_id=correlation_id,
            )
            self.metrics.append(record)
            _logger.info(
                "operation_latency_measured",
                operation=operation,
                duration_ms=record.value,
                correlation_id=correlation_id,
                **record.dimensions,
            )
        except Exception:
            _logger.warning("telemetry_latency_recording_suppressed")

    def record_counter(
        self,
        metric_name: str,
        value: float = 1.0,
        *,
        unit: MetricUnit = MetricUnit.COUNT,
        dimensions: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        try:
            dims = dict(dimensions or {})
            record = TelemetryMetricRecord(
                metric_name=metric_name,
                value=value,
                unit=unit,
                dimensions=dims,
                correlation_id=correlation_id,
            )
            self.metrics.append(record)
            _logger.info(
                "operational_counter_incremented",
                metric=metric_name,
                value=value,
                correlation_id=correlation_id,
                **record.dimensions,
            )
        except Exception:
            _logger.warning("telemetry_counter_recording_suppressed")

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
        try:
            self.costs.append(cost)
            # Record underlying CloudWatch-style metrics with bounded dimensions
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

            _logger.info(
                "model_token_usage_recorded",
                model_id=model_id,
                operation=operation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=cost.total_tokens,
                estimated_usd=cost.estimated_inference_cost_usd,
                actual_usd=cost.actual_billed_cost_usd,
                correlation_id=correlation_id,
            )
        except Exception:
            _logger.warning("telemetry_token_usage_recording_suppressed")
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
        try:
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
        except Exception:
            _logger.warning("telemetry_query_cost_recording_suppressed")

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
        entry = {
            "category": cat_str,
            "decision": decision,
            "reason": clean_reason,
            "correlation_id": correlation_id,
        }
        self.admission_decisions.append(entry)
        try:
            metric_name = (
                "AdmissionAllowed" if decision == "allowed" else "AdmissionDenied"
            )
            self.record_counter(
                metric_name,
                1.0,
                dimensions={"Category": cat_str, "AdmissionDecision": decision},
                correlation_id=correlation_id,
            )
            level = logging.INFO if decision == "allowed" else logging.WARNING
            _logger.log(
                level,
                "admission_decision_recorded",
                category=cat_str,
                decision=decision,
                reason=clean_reason,
                correlation_id=correlation_id,
            )
        except Exception:
            _logger.warning("telemetry_admission_recording_suppressed")

    def record_operational_signal(self, signal: OperationalSignal) -> None:
        try:
            self.signals.append(signal)
            _logger.info(
                "operational_signal_emitted",
                event_name=signal.event_name,
                stage=signal.stage,
                status=signal.status,
                duration_ms=signal.duration_ms,
                correlation_id=signal.correlation_id,
                **signal.safe_attributes,
            )
        except Exception:
            _logger.warning("telemetry_signal_recording_suppressed")
