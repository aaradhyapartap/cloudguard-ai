"""Synthetic corpus for bounded model-data minimization and detector contracts."""

from __future__ import annotations

import pytest
from app.security.sensitive_data import (
    DeterministicSensitiveDataDetector,
    SensitiveFinding,
    redact_sensitive_text,
)

SENSITIVE_SAMPLES = [
    "alice@example.test",
    "123-45-6789",
    "+14155552671",
    "(415) 555-2671",
    "415-555-2671",
    "AKIA" + "A" * 16,
    ".".join(("eyJhbGciOiJub25lIn0", "eyJzdWIiOiJzeW50aGV0aWMifQ", "synthetic")),
    "api_key=synthetic-credential-value",
    "task_token: synthetic-capability-value",
    "Bearer synthetic-bearer-value",
    "-----BEGIN PRIVATE KEY-----\nsynthetic-material\n-----END PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----\nsynthetic-unterminated-material",
    "https://example.test/file?X-Amz-Signature=synthetic-signature",
    "https://example.test/file?%58-Amz-Signature=synthetic-signature",
    "https://user:synthetic-password@example.test/file",
]


@pytest.mark.parametrize("sample", SENSITIVE_SAMPLES)
def test_sensitive_patterns_are_redacted_and_not_retained_in_findings(sample: str) -> None:
    result = redact_sensitive_text(sample)
    assert result.findings
    assert sample not in result.text
    assert sample not in repr(result.findings)
    assert not DeterministicSensitiveDataDetector().detect(result.text)


@pytest.mark.parametrize("sample", SENSITIVE_SAMPLES)
def test_length_preserving_redaction_supports_original_chunk_boundaries(sample: str) -> None:
    result = redact_sensitive_text(sample, preserve_length=True)
    assert len(result.text) == len(sample)
    assert sample not in result.text
    assert not DeterministicSensitiveDataDetector().detect(result.text)


def test_safe_identifiers_and_compliance_text_are_preserved() -> None:
    text = (
        '[S1] control_id=11111111-1111-4111-8111-111111111111 '
        'chunk_id=chunk-1 SOC 2 CC6.1 requires MFA. score=0.75'
    )
    result = redact_sensitive_text(text)
    assert result.text == text
    assert result.findings == ()


def test_overlapping_findings_are_merged_without_leaking_suffixes() -> None:
    text = "Bearer " + ".".join(
        ("eyJhbGciOiJub25lIn0", "eyJzdWIiOiJzeW50aGV0aWMifQ", "synthetic")
    )
    assert redact_sensitive_text(text).text == "[REDACTED]"


@pytest.mark.parametrize("start,end,category", [(-1, 2, "PII"), (0, 99, "PII"),
                                            (2, 1, "PII"), (0, 1, "unsafe-category")])
def test_invalid_detector_spans_fail_closed(start: int, end: int, category: str) -> None:
    class InvalidDetector:
        def detect(self, text: str) -> tuple[SensitiveFinding, ...]:
            return (SensitiveFinding(category, start, end),)

    with pytest.raises(ValueError, match="Invalid sensitive-data detector"):
        redact_sensitive_text("safe", detector=InvalidDetector())
