"""Public metadata is an allowlist, not a pass-through of stored metadata."""

from __future__ import annotations

import pytest
from app.security.projections import safe_source_metadata


def test_safe_metadata_preserves_useful_fields_without_mutating_original() -> None:
    original = {
        "page": 2, "chunk_index": 0, "confidentiality_level": "internal",
        "content_type": "application/pdf", "filename": "sensitive@example.test.pdf",
        "storage_key": "internal-object-key", "task_token": "internal-capability",
        "nested": {"credential": "synthetic-sensitive"},
        "model_data_policy_version": "model-data-v1",
        "sensitive_data_categories": ["SSN", "EMAIL", "SSN"],
    }
    result = safe_source_metadata(original)
    assert result == {
        "page": 2, "chunk_index": 0, "confidentiality_level": "internal",
        "content_type": "application/pdf", "model_data_policy_version": "model-data-v1",
        "sensitive_data_categories": ["EMAIL", "SSN"],
    }
    assert original["storage_key"] == "internal-object-key"
    assert original["sensitive_data_categories"] == ["SSN", "EMAIL", "SSN"]


@pytest.mark.parametrize("metadata", [
    {"page": True}, {"page": -1}, {"page": 1_000_001}, {"chunk_index": "1"},
    {"page": {"task_token": "synthetic"}}, {"confidentiality_level": "invented"},
    {"content_type": "text/plain; secret=synthetic"},
    {"model_data_policy_version": "untrusted-version"},
    {"sensitive_data_categories": ["synthetic-sensitive-value"]},
])
def test_invalid_or_attacker_selected_metadata_is_not_reflected(
    metadata: dict[str, object],
) -> None:
    assert safe_source_metadata(metadata) == {}
