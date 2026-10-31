"""Reusable deterministic prompt-injection corpus and authority regressions."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from app.models.compliance_agent import ComplianceToolIntent
from app.models.research_agent import ResearchToolIntent
from app.models.reviewer_agent import ReviewerModelOutput
from app.models.risk_agent import RiskModelOutput
from app.security.prompt_isolation import (
    UntrustedPromptSection,
    build_isolated_user_prompt,
)
from pydantic import BaseModel, ValidationError

CORPUS_PATH = (
    Path(__file__).parents[1] / "fixtures" / "security" / "prompt_injection.json"
)
SCHEMAS: dict[str, type[BaseModel]] = {
    "research": ResearchToolIntent,
    "compliance": ComplianceToolIntent,
    "risk": RiskModelOutput,
    "reviewer": ReviewerModelOutput,
}


def _corpus() -> list[dict[str, Any]]:
    raw = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return raw


@pytest.mark.parametrize("case", _corpus(), ids=lambda case: str(case["id"]))
def test_injection_payload_is_confined_to_nonce_framed_data(
    case: dict[str, Any],
) -> None:
    payload = str(case["payload"])
    prompt = build_isolated_user_prompt(
        task="Analyze admitted evidence.",
        user_request="Review the evidence.",
        trusted_identifiers={
            "allowed_chunk_ids": ["chunk-1"],
            "permitted_tools": ["search_documents"],
        },
        untrusted_sections=[
            UntrustedPromptSection(name="REFERENCE_EVIDENCE", content=payload)
        ],
        output_contract="Return only the supplied schema.",
    )

    nonce_match = re.search(r"^BOUNDARY NONCE: ([0-9a-f]{32})$", prompt, re.MULTILINE)
    assert nonce_match is not None
    nonce = nonce_match.group(1)
    start = f"BEGIN_UNTRUSTED_CONTEXT_REFERENCE_EVIDENCE_{nonce}\n"
    end = f"\nEND_UNTRUSTED_CONTEXT_REFERENCE_EVIDENCE_{nonce}"
    framed_payload = prompt.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]

    assert framed_payload == payload
    assert nonce not in payload


@pytest.mark.parametrize("case", _corpus(), ids=lambda case: str(case["id"]))
def test_injection_cannot_expand_model_output_authority(
    case: dict[str, Any],
) -> None:
    schema = SCHEMAS[str(case["target_schema"])]

    with pytest.raises(ValidationError):
        schema.model_validate(case["attempted_output"])
