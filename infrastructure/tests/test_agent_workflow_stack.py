"""Template assertions for the deterministic Phase 6 agent workflow."""

from __future__ import annotations

import json

from aws_cdk import assertions


def _definition_text(template: assertions.Template) -> str:
    resources = template.find_resources("AWS::StepFunctions::StateMachine")
    assert len(resources) == 1

    resource = next(iter(resources.values()))
    definition = resource["Properties"]["DefinitionString"]

    return json.dumps(definition, separators=(",", ":")).replace("\\", "")


def test_agent_workflow_has_three_scoped_lambdas(
    agent_workflow_template: assertions.Template,
) -> None:
    expected_commands = (
        "app.deployed_research_agent_worker.handler",
        "app.deployed_risk_agent_worker.handler",
        "app.deployed_reviewer_agent_worker.handler",
    )

    for command in expected_commands:
        agent_workflow_template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "PackageType": "Image",
                "ImageConfig": {"Command": [command]},
                "Environment": {
                    "Variables": assertions.Match.object_like(
                        {
                            "ENVIRONMENT": "dev",
                            "LLM_PROVIDER": "bedrock",
                            "ENABLE_AGENTIC_WORKFLOWS": "true",
                        }
                    )
                },
            },
        )


def test_agent_workflow_is_standard_state_machine(
    agent_workflow_template: assertions.Template,
) -> None:
    agent_workflow_template.has_resource_properties(
        "AWS::StepFunctions::StateMachine",
        {
            "StateMachineName": "cloudguard-agent-workflow-dev",
            "StateMachineType": "STANDARD",
            "EncryptionConfiguration": {"Type": "AWS_OWNED_KEY"},
        },
    )


def test_agent_workflow_graph_is_fixed_and_ordered(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template)

    assert '"Next":"ResearchAgent"' in definition
    assert '"ResearchAgent"' in definition
    assert '"Next":"RiskAgent"' in definition
    assert '"RiskAgent"' in definition
    assert '"Next":"ReviewerAgent"' in definition
    assert '"ReviewerAgent"' in definition
    assert '"Next":"ReviewerDecision"' in definition

    assert definition.index('"ResearchAgent"') < definition.index('"RiskAgent"')
    assert definition.index('"RiskAgent"') < definition.index('"ReviewerAgent"')
    assert definition.index('"ReviewerAgent"') < definition.index('"ReviewerDecision"')


def test_reviewer_fail_terminates_workflow(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template)

    assert '"Variable":"$.reviewer.decision"' in definition
    assert '"StringEquals":"PASS"' in definition
    assert '"Next":"WorkflowSucceeded"' in definition
    assert '"Default":"ReviewerFailed"' in definition
    assert '"ReviewerFailed"' in definition
    assert '"Type":"Fail"' in definition
    assert "ReviewerRejectedWorkflow" in definition


def test_phase6_contains_no_human_approval_wait(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template).lower()

    assert "waitfortasktoken" not in definition
    assert "tasktoken" not in definition
    assert "approval" not in definition


def test_agent_tasks_fail_closed(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template)

    assert "AgentExecutionFailed" in definition
    assert "States.ALL" in definition
