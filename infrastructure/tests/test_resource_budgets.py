"""Explicit concurrency and retry ceilings without shortening human approval."""

import json
from typing import Any

import pytest
from aws_cdk import assertions


def _definition(template: assertions.Template) -> dict[str, Any]:
    resource = next(iter(template.find_resources("AWS::StepFunctions::StateMachine").values()))
    value = resource["Properties"]["DefinitionString"]
    if isinstance(value, str):
        return json.loads(value)
    if "Fn::Join" in value:
        separator, parts = value["Fn::Join"]
        rendered = separator.join(part if isinstance(part, str) else "INTRINSIC" for part in parts)
        return json.loads(rendered)
    substitution = value["Fn::Sub"]
    return json.loads(substitution[0] if isinstance(substitution, list) else substitution)


@pytest.mark.parametrize("template_name", ["ingestion_template", "agent_workflow_template"])
def test_all_lambda_tasks_have_only_two_admission_throttle_retries(
    template_name: str, request: pytest.FixtureRequest,
) -> None:
    template = request.getfixturevalue(template_name)
    definition = _definition(template)
    tasks = [
        state for name, state in definition["States"].items()
        if state["Type"] == "Task" and name != "SendFailureToDLQ"
    ]
    assert len(tasks) == (1 if template_name == "ingestion_template" else 5)
    for task in tasks:
        assert task["Retry"] == [{
            "ErrorEquals": ["Lambda.TooManyRequestsException"],
            "IntervalSeconds": 2, "BackoffRate": 2, "MaxAttempts": 2,
        }]
        assert task["Catch"]  # Existing failure paths are still present.


def test_cost_bearing_worker_concurrency_is_reserved(
    ingestion_template: assertions.Template, agent_workflow_template: assertions.Template,
) -> None:
    for command in ("research_agent", "risk_agent", "reviewer_agent"):
        agent_workflow_template.has_resource_properties("AWS::Lambda::Function", {
            "ImageConfig": {"Command": [f"app.deployed_{command}_worker.handler"]},
            "ReservedConcurrentExecutions": 2, "Timeout": 120,
        })
    ingestion_template.has_resource_properties("AWS::Lambda::Function", {
        "ImageConfig": {"Command": ["app.deployed_document_worker.handler"]},
        "ReservedConcurrentExecutions": 2, "Timeout": 300,
    })


def test_approval_wait_and_ingestion_dlq_are_not_removed(
    ingestion_template: assertions.Template, agent_workflow_template: assertions.Template,
) -> None:
    agent = _definition(agent_workflow_template)
    assert agent["TimeoutSeconds"] == 31_536_000
    wait = agent["States"]["WaitForManagerApproval"]
    assert "waitForTaskToken" in wait["Resource"]
    assert wait["Parameters"]["Payload"]["task_token.$"] == "$$.Task.Token"
    ingestion = _definition(ingestion_template)
    assert ingestion["States"]["ProcessDocument"]["Catch"][0]["Next"] == "SendFailureToDLQ"
    ingestion_template.has_resource_properties("AWS::Events::Rule", {
        "Targets": assertions.Match.array_with([assertions.Match.object_like({
            "RetryPolicy": {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 3600},
            "DeadLetterConfig": assertions.Match.any_value(),
        })]),
    })
