"""Redaction must be automatic, not remembered."""

from __future__ import annotations

import io
import logging
from collections import UserDict

import pytest
import structlog
from app.core.logging import REDACTED, PrivacyLogFilter, _mark_sanitized, redact_sensitive


def test_top_level_sensitive_keys_are_redacted() -> None:
    result = redact_sensitive(None, "info", {"password": "hunter2", "user": "ana"})
    assert result["password"] == REDACTED
    assert result["user"] == "ana"


def test_nested_sensitive_keys_are_redacted() -> None:
    event = {"request": {"headers": {"authorization": "Bearer abc", "accept": "json"}}}
    result = redact_sensitive(None, "info", event)
    assert result["request"]["headers"]["authorization"] == REDACTED
    assert result["request"]["headers"]["accept"] == "json"


def test_lists_of_objects_are_traversed() -> None:
    event = {"items": [{"api_key": "sk-live-1"}, {"name": "safe"}]}
    result = redact_sensitive(None, "info", event)
    assert result["items"][0]["api_key"] == REDACTED
    assert result["items"][1]["name"] == "safe"


def test_matching_is_on_key_fragments_not_exact_names() -> None:
    event = {"x_access_token": "t", "refresh_token": "r", "session_id": "s"}
    result = redact_sensitive(None, "info", event)
    assert all(value == REDACTED for value in result.values())


def test_tuples_and_mappings_are_scrubbed_without_mutating_evidence() -> None:
    evidence = {"items": (UserDict({"email": "synthetic@example.test"}),)}
    result = redact_sensitive(None, "info", evidence)
    assert result["items"][0]["email"] == REDACTED
    assert evidence["items"][0]["email"] == "synthetic@example.test"


@pytest.mark.parametrize(
    "key",
    [
        "filename", "storage_key", "s3_key", "presigned_url", "system_prompt",
        "document_content", "document_body", "embedding", "api-key",
        "error", "detail", "exception", "stack", "body",
    ],
)
def test_sensitive_log_fields_are_redacted(key: str) -> None:
    result = redact_sensitive(None, "error", {key: "SYNTHETIC-SENSITIVE"})
    assert result[key] == REDACTED


def test_binary_exception_and_opaque_values_are_not_rendered() -> None:
    class Dangerous:
        def __repr__(self) -> str:
            raise AssertionError("Sensitive object must never be rendered")

    result = redact_sensitive(
        None, "info", {"values": [b"sensitive", ValueError("secret"), Dangerous()]}
    )
    assert result["values"] == [REDACTED, REDACTED, REDACTED]


def test_recursive_and_deep_structures_fail_closed() -> None:
    recursive: dict[str, object] = {}
    recursive["child"] = recursive
    result = redact_sensitive(None, "info", recursive)
    assert REDACTED in str(result)


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_renderers_cannot_restore_exception_or_stack_content(log_format: str) -> None:
    output = io.StringIO()
    renderer = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    logger = structlog.wrap_logger(
        structlog.PrintLogger(file=output),
        processors=[redact_sensitive, renderer],
    )
    try:
        raise ValueError("SYNTHETIC-SECRET-IN-EXCEPTION")
    except ValueError:
        logger.error("safe_failure", exc_info=True, stack_info=True, error_type="ValueError")

    rendered = output.getvalue()
    assert "SYNTHETIC-SECRET" not in rendered
    assert "Traceback" not in rendered
    assert "safe_failure" in rendered
    assert "ValueError" in rendered


def test_foreign_logging_drops_values_before_formatting_opaque_messages() -> None:
    class Dangerous:
        def __str__(self) -> str:
            raise AssertionError("A foreign message must not be formatted")

    record = logging.LogRecord("external", logging.ERROR, "", 1, Dangerous(), (), None)
    record.exc_text = "SYNTHETIC-SENSITIVE-TRACEBACK"
    record.stack_info = "SYNTHETIC-SENSITIVE-STACK"
    assert PrivacyLogFilter().filter(record)
    rendered = logging.Formatter("%(message)s").format(record)
    assert "external_log" in rendered
    assert "SYNTHETIC-SENSITIVE" not in rendered


def test_sanitized_application_events_survive_standard_logging_filter() -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(PrivacyLogFilter())
    logger = logging.Logger("privacy-test", level=logging.INFO)
    logger.addHandler(handler)
    structured = structlog.wrap_logger(
        logger,
        processors=[redact_sensitive, structlog.processors.JSONRenderer(), _mark_sanitized],
    )
    structured.info("safe_application_event", count=3, task_token="synthetic-capability")
    rendered = output.getvalue()
    assert "safe_application_event" in rendered
    assert '"count": 3' in rendered
    assert "synthetic-capability" not in rendered


def test_raw_route_is_never_added_to_request_context() -> None:
    from app.core.logging import bind_request_context
    from structlog.contextvars import clear_contextvars, get_contextvars

    try:
        bind_request_context(request_id="req-safe", route="/SYNTHETIC-SENSITIVE")
        assert "route" not in get_contextvars()
    finally:
        clear_contextvars()
