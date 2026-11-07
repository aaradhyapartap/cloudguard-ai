"""Fail-closed compliance scope contracts shared by API and agents."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.models.agents import GetPolicyArguments
from app.models.compliance import ComplianceCandidateExtractionRequest
from app.models.compliance_agent import ComplianceAgentRequest
from pydantic import ValidationError

ASSESSMENT_ID = UUID("11111111-1111-4111-8111-111111111111")


@pytest.mark.parametrize(
    "request_type",
    [
        ComplianceCandidateExtractionRequest,
        ComplianceAgentRequest,
        GetPolicyArguments,
    ],
)
def test_empty_control_scope_is_rejected(request_type: type[object]) -> None:
    with pytest.raises(ValidationError):
        request_type(assessment_id=ASSESSMENT_ID, control_ids=[])  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "request_type",
    [
        ComplianceCandidateExtractionRequest,
        ComplianceAgentRequest,
        GetPolicyArguments,
    ],
)
def test_omitted_control_scope_remains_explicit_all_controls(
    request_type: type[object],
) -> None:
    request = request_type(assessment_id=ASSESSMENT_ID)  # type: ignore[call-arg]
    assert request.control_ids is None
