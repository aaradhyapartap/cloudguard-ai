"""Redaction must be automatic, not remembered."""

from __future__ import annotations

import io
from collections import UserDict

import pytest
import structlog
from app.core.logging import REDACTED, redact_sensitive


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
