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

import json
import logging
import sys
from collections.abc import Mapping
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars
from structlog.typing import EventDict, WrappedLogger

from app.core.config import (
    AgentWorkerSettings,
    ApprovalWorkerSettings,
    Settings,
    WorkerSettings,
)

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
    "email",
    "filename",
    "file_name",
    "storage_key",
    "s3_key",
    "presigned",
    "prompt",
    "document_content",
    "document_body",
    "embedding",
)

_SENSITIVE_EXACT_KEYS = frozenset({"error", "detail", "exception", "stack", "body"})
_MAX_REDACTION_DEPTH = 12


def _mark_sanitized(
    _logger: WrappedLogger, _name: str, rendered: object
) -> tuple[tuple[str], dict[str, Any]]:
    """Mark only output that has traversed the application processor chain."""
    if not isinstance(rendered, str):
        raise TypeError("Expected rendered log text.")
    return ((rendered,), {"extra": {"_cloudguard_sanitized": True}})


class PrivacyLogFilter(logging.Filter):
    """Discard foreign free text before any formatter invokes getMessage()."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "_cloudguard_sanitized", False):
            record.msg = json.dumps({
                "event": "external_log",
                "level": logging.getLevelName(record.levelno),
            })
            record.args = ()
        # Never let the standard formatter append traceback/stack text, even if
        # a caller adds exception state after application rendering.
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def redact_sensitive(
    _logger: WrappedLogger, _name: str, event_dict: EventDict
) -> EventDict:
    """Scrub structured application events without rendering opaque objects.

    Free-text value detection is a separate defense; callers must use static event
    names and safe identifiers, never exception strings or document text.
    """

    def _scrub(value: Any, key: str = "", depth: int = 0) -> Any:
        lowered = key.lower().replace("-", "_")
        if (
            depth >= _MAX_REDACTION_DEPTH
            or lowered in _SENSITIVE_EXACT_KEYS
            or any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)
        ):
            return REDACTED
        if isinstance(value, Mapping):
            return {
                k: _scrub(v, k, depth + 1)
                for k, v in value.items()
                if isinstance(k, str)
            }
        if isinstance(value, (list, tuple)):
            return [_scrub(item, depth=depth + 1) for item in value]
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        return REDACTED

    # Never let a renderer reconstruct an exception, traceback, or stack after
    # redaction. Chained exceptions can contain credentials and document data.
    return {
        key: _scrub(value, key)
        for key, value in event_dict.items()
        if key not in {"exc_info", "stack_info"}
    }


def configure_logging(
    settings: Settings | WorkerSettings | AgentWorkerSettings | ApprovalWorkerSettings,
) -> None:
    """Idempotent. Safe to call from both the ASGI app and the Lambda handler."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
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
        processors=[*shared, renderer, _mark_sanitized],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(PrivacyLogFilter())
    logging.basicConfig(
        format="%(message)s",
        handlers=[handler],
        level=level,
        force=True,
    )
    for noisy in (
        "uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy", "sqlalchemy.engine",
        "botocore", "boto3", "httpx", "httpcore", "mangum", "mangum.http", "mangum.lifespan",
    ):
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
    # Kept as an ignored compatibility argument; raw paths may contain secrets.
    del route
    bind_contextvars(**context)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
