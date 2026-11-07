"""Provider-neutral sensitive-data detection and model-input minimization.

This bounded pattern policy is defense in depth, not a universal PII recognizer.
Findings hold offsets and categories only, never copies of sensitive values.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

from app.core.logging import get_logger

POLICY_VERSION = "model-data-v1"
logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SensitiveFinding:
    category: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str
    findings: tuple[SensitiveFinding, ...]


class SensitiveDataDetector(Protocol):
    """A local or future provider-backed detector must return validated spans."""

    def detect(self, text: str) -> tuple[SensitiveFinding, ...]: ...


_PATTERNS = (
    ("EMAIL", re.compile(
        r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
        r"@[A-Z0-9-]{1,63}(?:\.[A-Z0-9-]{1,63}){1,6}(?![\w.-])", re.IGNORECASE
    )),
    ("SSN", re.compile(r"(?<![\w-])[0-9]{3}-[0-9]{2}-[0-9]{4}(?![\w-])")),
    ("PHONE", re.compile(
        r"(?<![\w+-])(?:\+[1-9][0-9]{7,14}|"
        r"(?:\+1[ .-]?)?(?:\([0-9]{3}\)[ .-]?|[0-9]{3}[ .-])"
        r"[0-9]{3}[ .-]?[0-9]{4})(?![\w-])"
    )),
    ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("JWT", re.compile(
        r"\beyJ[A-Za-z0-9_-]{2,2048}\.[A-Za-z0-9_-]{1,8192}\."
        r"[A-Za-z0-9_-]{1,2048}\b"
    )),
    ("CREDENTIAL", re.compile(
        r"\b(?:password|passwd|api[_-]?key|secret|aws_secret_access_key|"
        r"task[_-]?token|access[_-]?token|refresh[_-]?token)"
        r"[\"']?\s*[:=]\s*[\"']?[^\s\"',;}\]]{8,}", re.IGNORECASE
    )),
    ("BEARER", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*", re.IGNORECASE)),
    ("PRIVATE_KEY", re.compile(
        r"-----BEGIN (?:[A-Z0-9 ]{0,32})PRIVATE KEY-----.*?"
        r"(?:-----END (?:[A-Z0-9 ]{0,32})PRIVATE KEY-----|\Z)", re.DOTALL
    )),
)
_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SIGNED_QUERY_KEYS = frozenset({
    "x-amz-signature", "x-amz-credential", "x-amz-security-token",
    "awsaccesskeyid", "signature", "sig", "token", "access_token",
})


class DeterministicSensitiveDataDetector:
    """Recognize explicitly documented formats without any network request."""

    def detect(self, text: str) -> tuple[SensitiveFinding, ...]:
        findings = [
            SensitiveFinding(category, match.start(), match.end())
            for category, pattern in _PATTERNS
            for match in pattern.finditer(text)
        ]
        for match in _URL_PATTERN.finditer(text):
            try:
                parsed = urlsplit(match.group())
                keys = {key.lower() for key, _value in parse_qsl(parsed.query)}
                if parsed.username is not None or keys & _SIGNED_QUERY_KEYS:
                    findings.append(SensitiveFinding("SENSITIVE_URL", match.start(), match.end()))
            except ValueError:
                # A malformed URL-like value is not safe to send to a model.
                findings.append(SensitiveFinding("SENSITIVE_URL", match.start(), match.end()))
        return tuple(sorted(findings, key=lambda item: (item.start, -item.end, item.category)))


def redact_sensitive_text(
    text: str, *, detector: SensitiveDataDetector | None = None, preserve_length: bool = False
) -> RedactionResult:
    """Replace merged spans in a copy; preserve the original evidence unchanged."""
    findings = (detector or DeterministicSensitiveDataDetector()).detect(text)
    for finding in findings:
        if (
            not 0 <= finding.start < finding.end <= len(text)
            or re.fullmatch(r"[A-Z_]{1,32}", finding.category) is None
        ):
            raise ValueError("Invalid sensitive-data detector finding")

    merged: list[tuple[int, int]] = []
    for finding in sorted(findings, key=lambda item: (item.start, item.end)):
        if merged and finding.start <= merged[-1][1]:
            start, end = merged[-1]
            merged[-1] = (start, max(end, finding.end))
        else:
            merged.append((finding.start, finding.end))

    parts: list[str] = []
    position = 0
    for start, end in merged:
        replacement = "*" * (end - start) if preserve_length else "[REDACTED]"
        parts.extend((text[position:start], replacement))
        position = end
    parts.append(text[position:])
    return RedactionResult("".join(parts), findings)


def minimize_model_text(text: str, *, surface: str, preserve_length: bool = False) -> str:
    """Minimize model input and emit only safe decision metadata for auditability."""
    result = redact_sensitive_text(text, preserve_length=preserve_length)
    if result.findings:
        counts = Counter(finding.category for finding in result.findings)
        logger.info(
            "model_input_minimized",
            policy_version=POLICY_VERSION,
            surface=surface,
            categories=[{"category": category, "count": count}
                        for category, count in sorted(counts.items())],
        )
    return result.text
