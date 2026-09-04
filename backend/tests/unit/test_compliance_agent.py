"""Unit tests for bounded Compliance Agent contracts."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.models.agents import ToolName
from app.models.ai import TokenUsage
from app.models.compliance_agent import (
    ComplianceAgentRequest,
    ComplianceAgentResult,
    ComplianceFindingEnvelope,
    ComplianceFindingProposal,
    ComplianceSearchArguments,
    ComplianceToolIntent,
)
from pydantic import ValidationError

ASSESSMENT_ID = UUID("11111111-1111-4111-8111-111111111111")
CONTROL_ID = UUID("33333333-3333-4333-8333-333333333333")


def test_compliance_agent_request_strips_query_hint() -> None:
    request = ComplianceAgentRequest(
        assessment_id=ASSESSMENT_ID,
        control_ids=[CONTROL_ID],
        query_hint="  access control policy  ",
        top_k=5,
    )

    assert request.query_hint == "access control policy"


def test_compliance_agent_request_blank_query_hint_becomes_none() -> None:
    request = ComplianceAgentRequest(
        assessment_id=ASSESSMENT_ID,
        query_hint="   ",
    )

    assert request.query_hint is None


def test_compliance_agent_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ComplianceAgentRequest.model_validate(
            {
                "assessment_id": str(ASSESSMENT_ID),
                "organization_id": "99999999-9999-4999-8999-999999999999",
            }
        )


def test_compliance_agent_request_rejects_more_than_25_controls() -> None:
    control_ids = [
        UUID(f"00000000-0000-4000-8000-{index:012d}")
        for index in range(26)
    ]

    with pytest.raises(ValidationError):
        ComplianceAgentRequest(
            assessment_id=ASSESSMENT_ID,
            control_ids=control_ids,
        )


def test_compliance_agent_request_rejects_out_of_range_top_k() -> None:
    with pytest.raises(ValidationError):
        ComplianceAgentRequest(
            assessment_id=ASSESSMENT_ID,
            top_k=11,
        )


def test_compliance_search_arguments_normalize_query() -> None:
    arguments = ComplianceSearchArguments(
        query="  privileged access policy  ",
        top_k=3,
    )

    assert arguments.query == "privileged access policy"
    assert arguments.top_k == 3


def test_compliance_tool_intent_accepts_only_search_documents() -> None:
    intent = ComplianceToolIntent(
        tool_name=ToolName.SEARCH_DOCUMENTS,
        arguments=ComplianceSearchArguments(
            query="access control evidence",
            top_k=5,
        ),
    )

    assert intent.tool_name is ToolName.SEARCH_DOCUMENTS


def test_compliance_tool_intent_rejects_out_of_scope_tool() -> None:
    with pytest.raises(ValidationError):
        ComplianceToolIntent.model_validate(
            {
                "tool_name": "delete_document",
                "arguments": {
                    "query": "policy",
                    "top_k": 5,
                },
            }
        )


def test_compliance_tool_intent_rejects_model_supplied_tenant_identity() -> None:
    with pytest.raises(ValidationError):
        ComplianceToolIntent.model_validate(
            {
                "tool_name": "search_documents",
                "arguments": {
                    "query": "policy",
                    "top_k": 5,
                    "organization_id": "99999999-9999-4999-8999-999999999999",
                },
            }
        )


def test_compliance_finding_proposal_is_bounded() -> None:
    proposal = ComplianceFindingProposal(
        control_id=CONTROL_ID,
        proposed_status="satisfied",
        rationale="The retrieved evidence supports the control.",
        evidence_sources=["S1"],
        confidence=0.95,
    )

    assert proposal.evidence_sources == ["S1"]
    assert proposal.confidence == 0.95


def test_compliance_finding_proposal_rejects_more_than_five_sources() -> None:
    with pytest.raises(ValidationError):
        ComplianceFindingProposal(
            control_id=CONTROL_ID,
            proposed_status="satisfied",
            rationale="Evidence supports the control.",
            evidence_sources=["S1", "S2", "S3", "S4", "S5", "S6"],
        )


def test_compliance_finding_envelope_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ComplianceFindingEnvelope.model_validate(
            {
                "findings": [],
                "authoritative_score": 100,
            }
        )


def test_compliance_agent_result_is_bounded_and_immutable() -> None:
    result = ComplianceAgentResult(
        assessment_id=ASSESSMENT_ID,
        findings=[],
        retrieval_count=0,
        tool_calls_used=0,
        planning_model_id="mock:planner-v1",
        evaluation_model_id="mock:evaluator-v1",
        planning_usage=TokenUsage(
            input_tokens=10,
            output_tokens=5,
        ),
        evaluation_usage=TokenUsage(
            input_tokens=20,
            output_tokens=10,
        ),
    )

    assert result.findings == []
    assert result.planning_model_id == "mock:planner-v1"
    assert result.evaluation_model_id == "mock:evaluator-v1"

    with pytest.raises(ValidationError):
        result.__setattr__("planning_model_id", "other-model")


def test_compliance_agent_result_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ComplianceAgentResult.model_validate(
            {
                "assessment_id": str(ASSESSMENT_ID),
                "findings": [],
                "retrieval_count": 0,
                "tool_calls_used": 0,
                "planning_model_id": "mock:planner-v1",
                "evaluation_model_id": "mock:evaluator-v1",
                "planning_usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                },
                "evaluation_usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                },
                "authoritative_score": 99,
            }
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("retrieval_count", 11),
        ("tool_calls_used", 3),
    ],
)
def test_compliance_agent_result_rejects_out_of_range_counts(
    field_name: str,
    invalid_value: int,
) -> None:
    payload: dict[str, object] = {
        "assessment_id": str(ASSESSMENT_ID),
        "findings": [],
        "retrieval_count": 0,
        "tool_calls_used": 0,
        "planning_model_id": "mock:planner-v1",
        "evaluation_model_id": "mock:evaluator-v1",
        "planning_usage": {
            "input_tokens": 1,
            "output_tokens": 1,
        },
        "evaluation_usage": {
            "input_tokens": 1,
            "output_tokens": 1,
        },
    }
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        ComplianceAgentResult.model_validate(payload)
