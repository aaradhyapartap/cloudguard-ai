"""Structured logging.

Two decisions worth understanding:

1. **JSON, not formatted strings.** CloudWatch Logs Insights can query JSON
   fields directly (``fields @timestamp, organization_id | filter level="error"``).
   A human-readable string is a string; a JSON object is queryable telemetry.
   Locally we render colourised console output instead, because grep-ing JSON by
   eye is miserable.

2. **Redaction is a processor, not a convention.** "Remember not to log the
   token" is not a control. A processor that strips known-sensitive keys on
   every event, everywhere, is. It runs last so it cannot be bypassed by a
   caller binding context earlier in the chain.

Correlation: ``bind_request_context`` puts ``request_id``, ``organization_id``
and ``user_id`` into a context variable. Every subsequent log line in that
request carries them without being passed them, which is what makes a single
request traceable across the whole application.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars
from structlog.typing import EventDict, WrappedLogger

from app.core.config import AgentWorkerSettings, Settings, WorkerSettings

REDACTED = "[redacted]"

# Substrings, not exact keys — "authorization", "auth_header" and
# "x-authorization" should all be caught by one entry.
SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "cookie",
    "session_id",
    "private_key",
    "access_key",
    "ssn",
)


def redact_sensitive(
    _logger: WrappedLogger, _name: str, event_dict: EventDict
) -> EventDict:
    """Replace values whose key looks sensitive. Runs on every event."""

    def _scrub(value: Any, key: str = "") -> Any:
        lowered = key.lower()
        if any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS):
            return REDACTED
        if isinstance(value, dict):
            return {k: _scrub(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [_scrub(item) for item in value]
        return value

    return {key: _scrub(value, key) for key, value in event_dict.items()}


def configure_logging(settings: Settings | WorkerSettings | AgentWorkerSettings) -> None:
    """Idempotent. Safe to call from both the ASGI app and the Lambda handler."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_sensitive,  # last before rendering — cannot be bypassed
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    level = logging.getLevelNamesMapping()[settings.log_level]

    # stdlib LoggerFactory (not PrintLoggerFactory): structlog's
    # `add_logger_name` processor reads `logger.name`, which only a stdlib
    # logger has. It also means uvicorn, sqlalchemy and application logs all
    # travel through one handler and come out in one format.
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )
    for noisy in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True


def bind_request_context(
    *,
    request_id: str,
    organization_id: str | None = None,
    user_id: str | None = None,
    route: str | None = None,
) -> None:
    """Attach identifiers to every log line emitted for the rest of this request."""
    clear_contextvars()
    context: dict[str, str] = {"request_id": request_id}
    if organization_id:
        context["organization_id"] = organization_id
    if user_id:
        context["user_id"] = user_id
    if route:
        context["route"] = route
    bind_contextvars(**context)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
