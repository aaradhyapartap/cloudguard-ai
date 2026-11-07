"""Workflow logs must not copy payloads or grant workloads history access."""

from __future__ import annotations

from fnmatch import fnmatchcase

import pytest
from aws_cdk import assertions


@pytest.mark.parametrize("fixture_name", ["ingestion_template", "agent_workflow_template"])
def test_workflow_cloudwatch_payload_logging_is_explicitly_disabled(
    request: pytest.FixtureRequest, fixture_name: str,
) -> None:
    template: assertions.Template = request.getfixturevalue(fixture_name)
    resources = template.find_resources("AWS::StepFunctions::StateMachine")
    assert len(resources) == 1
    for resource in resources.values():
        assert resource["Properties"]["LoggingConfiguration"] == {
            "Level": "OFF", "IncludeExecutionData": False,
        }


@pytest.mark.parametrize("fixture_name", ["ingestion_template", "agent_workflow_template"])
def test_inline_workload_policies_do_not_grant_execution_payload_reads(
    request: pytest.FixtureRequest, fixture_name: str,
) -> None:
    template: assertions.Template = request.getfixturevalue(fixture_name)
    policies = template.find_resources("AWS::IAM::Policy")
    assert policies
    for resource in policies.values():
        statements = resource["Properties"]["PolicyDocument"]["Statement"]
        for statement in statements:
            if statement.get("Effect") != "Allow":
                continue
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            for action in actions:
                for restricted in ("states:getexecutionhistory", "states:describeexecution"):
                    assert not fnmatchcase(restricted, action.lower())
