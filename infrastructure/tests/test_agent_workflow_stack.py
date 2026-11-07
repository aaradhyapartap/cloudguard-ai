"""Template assertions for the deterministic Phase 7 agent workflow."""

from __future__ import annotations

import json

from aws_cdk import assertions


def _definition_text(template: assertions.Template) -> str:
    resources = template.find_resources(
        "AWS::StepFunctions::StateMachine"
    )
    assert len(resources) == 1

    resource = next(iter(resources.values()))
    definition = resource["Properties"]["DefinitionString"]

    return json.dumps(
        definition,
        separators=(",", ":"),
    ).replace("\\", "")


def test_agent_workflow_has_six_scoped_lambdas(
    agent_workflow_template: assertions.Template,
) -> None:
    expected_commands = (
        "app.deployed_research_agent_worker.handler",
        "app.deployed_risk_agent_worker.handler",
        "app.deployed_reviewer_agent_worker.handler",
        "app.deployed_approval_creation_worker.handler",
        "app.deployed_approval_callback_recovery_worker.handler",
        "app.deployed_approved_action_execution_worker.handler",
    )

    for command in expected_commands:
        agent_workflow_template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "PackageType": "Image",
                "ImageConfig": {
                    "Command": [command],
                },
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
            "EncryptionConfiguration": {
                "Type": "AWS_OWNED_KEY",
            },
        },
    )


def test_agent_workflow_graph_is_fixed_and_ordered(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template)

    assert '"Next":"ResearchAgent"' in definition
    assert '"Next":"RiskAgent"' in definition
    assert '"Next":"ReviewerAgent"' in definition
    assert '"Next":"ReviewerDecision"' in definition

    assert (
        definition.index('"ResearchAgent"')
        < definition.index('"RiskAgent"')
    )
    assert (
        definition.index('"RiskAgent"')
        < definition.index('"ReviewerAgent"')
    )
    assert (
        definition.index('"ReviewerAgent"')
        < definition.index('"ReviewerDecision"')
    )


def test_reviewer_pass_enters_human_approval_wait(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template)

    assert '"Variable":"$.reviewer.decision"' in definition
    assert '"StringEquals":"PASS"' in definition
    assert '"Next":"WaitForManagerApproval"' in definition

    assert "lambda:invoke.waitForTaskToken" in definition
    assert "$$.Task.Token" in definition
    assert '"ResultPath":"$.approval_decision"' in definition
    assert '"TimeoutSeconds":31536000' in definition


def test_reviewer_fail_terminates_before_approval(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template)

    assert '"Default":"ReviewerFailed"' in definition
    assert '"ReviewerFailed"' in definition
    assert '"Type":"Fail"' in definition
    assert "ReviewerRejectedWorkflow" in definition


def test_human_rejection_is_non_executing_terminal_path(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template)

    assert '"Variable":"$.approval_decision.decision"' in definition
    assert '"StringEquals":"rejected"' in definition
    assert '"Next":"WorkflowRejected"' in definition
    assert '"WorkflowRejected"' in definition


def test_approved_and_modified_enter_bounded_execution_boundary(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template)

    assert '"StringEquals":"approved"' in definition
    assert '"StringEquals":"modified"' in definition
    assert '"Next":"ExecuteApprovedAction"' in definition
    assert '"ExecuteApprovedAction"' in definition
    assert '"WorkflowExecuted"' in definition
    assert '"ApprovedActionExecutionFailed"' in definition


def test_approval_lambdas_have_data_api_permissions(
    agent_workflow_template: assertions.Template,
) -> None:
    policies = agent_workflow_template.find_resources(
        "AWS::IAM::Policy"
    )
    serialized = json.dumps(
        policies,
        separators=(",", ":"),
    )

    assert "rds-data:BeginTransaction" in serialized
    assert "rds-data:ExecuteStatement" in serialized
    assert "rds-data:CommitTransaction" in serialized
    assert "rds-data:RollbackTransaction" in serialized
    assert "secretsmanager:GetSecretValue" in serialized


def test_callback_recovery_has_stepfunctions_permission(
    agent_workflow_template: assertions.Template,
) -> None:
    policies = agent_workflow_template.find_resources(
        "AWS::IAM::Policy"
    )
    serialized = json.dumps(
        policies,
        separators=(",", ":"),
    )

    assert "states:SendTaskSuccess" in serialized


def test_callback_recovery_is_scheduled(
    agent_workflow_template: assertions.Template,
) -> None:
    rules = agent_workflow_template.find_resources(
        "AWS::Events::Rule"
    )
    serialized = json.dumps(
        rules,
        separators=(",", ":"),
    )

    assert "rate(1 minute)" in serialized


def test_agent_tasks_fail_closed(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template)

    assert "AgentExecutionFailed" in definition
    assert "ApprovalWaitFailed" in definition
    assert "States.ALL" in definition


def test_execution_task_forwards_no_action_authority(
    agent_workflow_template: assertions.Template,
) -> None:
    definition = _definition_text(agent_workflow_template)

    assert '"ExecuteApprovedAction"' in definition
    assert '"execution_id.$":"$.execution_id"' in definition
    assert '"principal.$":"$.principal"' in definition

    # The trusted worker reloads the action from Aurora.
    assert '"effective_action.$"' not in definition
    assert '"action.$"' not in definition


def test_execution_lambda_has_scoped_aurora_permissions(
    agent_workflow_template: assertions.Template,
) -> None:
    template = agent_workflow_template.to_json()
    resources = template["Resources"]

    execution_role = None

    for resource in resources.values():
        if resource.get("Type") != "AWS::Lambda::Function":
            continue

        properties = resource.get("Properties", {})
        image_config = properties.get("ImageConfig", {})

        if image_config.get("Command") != [
            "app.deployed_approved_action_execution_worker.handler"
        ]:
            continue

        role = properties["Role"]
        execution_role = role["Fn::GetAtt"][0]
        break

    assert execution_role is not None

    role_policies = []

    for resource in resources.values():
        if resource.get("Type") != "AWS::IAM::Policy":
            continue

        properties = resource.get("Properties", {})
        roles = properties.get("Roles", [])

        if {"Ref": execution_role} in roles:
            role_policies.append(
                properties["PolicyDocument"]
            )

    serialized = json.dumps(
        role_policies,
        separators=(",", ":"),
    )

    assert "rds-data:BeginTransaction" in serialized
    assert "rds-data:ExecuteStatement" in serialized
    assert "rds-data:CommitTransaction" in serialized
    assert "rds-data:RollbackTransaction" in serialized
    assert "secretsmanager:GetSecretValue" in serialized

    # Workflow-resume authority belongs only to callback recovery.
    assert "states:SendTaskSuccess" not in serialized
