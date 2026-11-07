"""Only cost-bearing workers receive namespaced admission-table permissions."""

import ast
from pathlib import Path
from typing import Any

import pytest
from aws_cdk import assertions

ADMISSION_ARN = "arn:aws:dynamodb:us-east-1:123456789012:table/test-admission"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


@pytest.mark.parametrize("fixture_name", ["ingestion_template", "agent_workflow_template"])
def test_worker_admission_environment_and_least_privilege_are_wired(
    fixture_name: str, request: pytest.FixtureRequest,
) -> None:
    template: assertions.Template = request.getfixturevalue(fixture_name)
    functions = template.find_resources("AWS::Lambda::Function")
    policies = template.find_resources("AWS::IAM::Policy")
    for function in functions.values():
        properties = function["Properties"]
        command = properties.get("ImageConfig", {}).get("Command", [None])[0]
        cost_bearing = command in {
            "app.deployed_document_worker.handler", "app.deployed_research_agent_worker.handler",
            "app.deployed_risk_agent_worker.handler", "app.deployed_reviewer_agent_worker.handler",
        }
        role_id = properties["Role"]["Fn::GetAtt"][0]
        statements = [
            statement for policy in policies.values()
            if {"Ref": role_id} in policy["Properties"]["Roles"]
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]
            if any(action.startswith("dynamodb:") for action in _as_list(statement["Action"]))
        ]
        if not cost_bearing:
            assert statements == []  # No new grants on approval/callback/action workers.
            assert "RATE_LIMIT_TABLE_NAME" not in properties.get("Environment", {}).get("Variables", {})
            continue
        assert properties["Environment"]["Variables"]["RATE_LIMIT_TABLE_NAME"] == "test-admission"
        assert len(statements) == 2
        expected = {
            "worker:v1:*": {"dynamodb:UpdateItem"},
            "slot:v1:*": {"dynamodb:PutItem", "dynamodb:DeleteItem"},
        }
        for statement in statements:
            assert statement["Effect"] == "Allow"
            assert _as_list(statement["Resource"]) == [ADMISSION_ARN]
            prefixes = _as_list(
                statement["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"],
            )
            assert len(prefixes) == 1
            assert set(_as_list(statement["Action"])) == expected[prefixes[0]]


def test_lease_duration_exceeds_every_cost_bearing_lambda_runtime(
    ingestion_template: assertions.Template, agent_workflow_template: assertions.Template,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    source = repository / "backend" / "app" / "services" / "work_admission.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    lease_seconds = next(
        ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "LEASE_SECONDS"
            for target in node.targets
        )
    )
    timeouts = [
        function["Properties"]["Timeout"]
        for template in (ingestion_template, agent_workflow_template)
        for function in template.find_resources("AWS::Lambda::Function").values()
        if "RATE_LIMIT_TABLE_NAME" in function["Properties"].get("Environment", {}).get("Variables", {})
    ]
    assert len(timeouts) == 4
    assert lease_seconds >= max(timeouts) + 60
