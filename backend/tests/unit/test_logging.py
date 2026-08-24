"""Redaction must be automatic, not remembered."""

from __future__ import annotations

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
