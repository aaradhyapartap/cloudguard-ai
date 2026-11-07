"""Mock telemetry adapter for deterministic testing."""

from __future__ import annotations

from app.adapters.local.telemetry import InMemoryTelemetryEmitter


class MockTelemetryEmitter(InMemoryTelemetryEmitter):
    """Alias for InMemoryTelemetryEmitter with mock naming convention."""
    pass
