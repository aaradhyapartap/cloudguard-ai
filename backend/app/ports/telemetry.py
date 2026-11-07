"""Provider-neutral operational telemetry emitter port.

Design rules:
- Provider-neutral contract; implementations reside in adapters.
- Metric dimensions are bounded; high-cardinality or sensitive dimensions are rejected.
- Telemetry failures must never mask application errors or bypass controls.
- Prompts, evidence bodies, tokens, credentials, and raw errors are strictly omitted.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.telemetry import (
    AdmissionCategory,
    CostEstimate,
    MetricUnit,
    OperationalSignal,
)


class TelemetryEmitter(ABC):
    """Abstract port for emitting operational metrics, latency, token usage, and costs."""

    @abstractmethod
    def record_latency(
        self,
        operation: str,
        duration_ms: float,
        *,
        dimensions: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Record operational latency for a named pipeline stage or service call."""

    @abstractmethod
    def record_counter(
        self,
        metric_name: str,
        value: float = 1.0,
        *,
        unit: MetricUnit = MetricUnit.COUNT,
        dimensions: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Record an incremental counter (e.g. invocations, errors, cache hits)."""

    @abstractmethod
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
        """Record model input/output token usage and estimate inference cost."""

    @abstractmethod
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

    @abstractmethod
    def record_admission_decision(
        self,
        *,
        category: AdmissionCategory | str,
        decision: str,  # "allowed" or "denied"
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Record rate-limit or worker-admission outcomes."""

    @abstractmethod
    def record_operational_signal(self, signal: OperationalSignal) -> None:
        """Emit a structured operational lifecycle signal."""
