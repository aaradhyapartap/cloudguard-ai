"""Counter storage and least-privilege admission grants, without deployments."""

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_iam as iam
from stacks.rate_limit_stack import RateLimitStack


def test_shared_counters_have_ttl_encryption_and_retention() -> None:
    app = cdk.App()
    stack = RateLimitStack(app, "TestLimits", environment_name="dev")
    template = assertions.Template.from_stack(stack)
    template.resource_count_is("AWS::DynamoDB::Table", 1)
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "BillingMode": "PAY_PER_REQUEST",
        "KeySchema": [{"AttributeName": "counter_key", "KeyType": "HASH"}],
        "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
        "SSESpecification": {"SSEEnabled": True},
        "DeletionProtectionEnabled": True,
    })
    table = next(iter(template.find_resources("AWS::DynamoDB::Table").values()))
    assert table["DeletionPolicy"] == "Retain"
    assert table["UpdateReplacePolicy"] == "Retain"


def test_api_grant_is_update_only_and_table_scoped() -> None:
    app = cdk.App()
    stack = RateLimitStack(app, "TestGrant", environment_name="dev")
    role = iam.Role(stack, "ApiRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
    stack.grant_admission(role)
    template = assertions.Template.from_stack(stack)
    policies = template.find_resources("AWS::IAM::Policy")
    statements = [
        statement for policy in policies.values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if statement.get("Effect") == "Allow"
    ]
    assert len(statements) == 1
    assert statements[0]["Action"] == "dynamodb:UpdateItem"
    logical_id = next(iter(template.find_resources("AWS::DynamoDB::Table")))
    resources = statements[0]["Resource"]
    if not isinstance(resources, list):
        resources = [resources]
    assert resources == [{"Fn::GetAtt": [logical_id, "Arn"]}]
