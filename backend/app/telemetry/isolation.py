"""Fail-safe telemetry invocation helper for boundary isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger

_logger = get_logger("app.telemetry.isolation")


def safe_telemetry_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute a telemetry emitter call with fail-safe isolation.

    Guarantees:
    - Never raises under any condition.
    - Suppresses errors and logs a structured warning.
    - Prevents telemetry defects from altering application control flow,
      masking original exceptions, or interfering with resource cleanup.
    """
    try:
        return fn(*args, **kwargs)
    except Exception:
        _logger.warning("telemetry_invocation_suppressed")
        return None

