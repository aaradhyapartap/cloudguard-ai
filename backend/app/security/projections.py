"""Allowlisted public source metadata; original authorized evidence stays intact."""

from __future__ import annotations

from typing import Any

_CONTENT_TYPES = frozenset({"text/plain", "application/pdf"})
_LEVELS = frozenset({"public", "internal", "confidential", "restricted"})
_CATEGORIES = frozenset({
    "EMAIL", "SSN", "PHONE", "AWS_ACCESS_KEY", "JWT", "CREDENTIAL",
    "BEARER", "PRIVATE_KEY", "SENSITIVE_URL",
})


def safe_source_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Project useful bounded fields, never arbitrary keys, strings, or objects.

    Confidentiality metadata is informational only. Admission still uses the
    original Principal and authoritative database document confidentiality.
    """
    projected: dict[str, Any] = {}
    for key, minimum in (("page", 1), ("chunk_index", 0)):
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= 1_000_000:
            projected[key] = value
    content_type = metadata.get("content_type")
    if isinstance(content_type, str) and content_type in _CONTENT_TYPES:
        projected["content_type"] = content_type
    level = metadata.get("confidentiality_level")
    if isinstance(level, str) and level in _LEVELS:
        projected["confidentiality_level"] = level
    if metadata.get("model_data_policy_version") == "model-data-v1":
        projected["model_data_policy_version"] = "model-data-v1"
    categories = metadata.get("sensitive_data_categories")
    if isinstance(categories, list) and len(categories) <= len(_CATEGORIES):
        if all(isinstance(value, str) and value in _CATEGORIES for value in categories):
            projected["sensitive_data_categories"] = sorted(set(categories))
    return projected
