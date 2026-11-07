"""Template assertions for the GitHub Actions OIDC stack and S3 Vectors grants."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from stacks.agent_workflow_stack import AgentWorkflowStack
from stacks.ingestion_stack import IngestionStack
from stacks.oidc_stack import (
    GITHUB_OIDC_CLIENT_ID,
    GITHUB_OIDC_THUMBPRINTS,
    GITHUB_OIDC_URL,
    GitHubOIDCStack,
)


def _create_oidc_stack(
    *,
    environment_name: str = "dev",
    cdk_qualifier: str = "hnb659fds",
    existing_provider_arn: str | None = None,
) -> tuple[cdk.App, GitHubOIDCStack, assertions.Template]:
    app = cdk.App()
    stack = GitHubOIDCStack(
        app,
        "TestGitHubOIDC",
        environment_name=environment_name,
        cdk_qualifier=cdk_qualifier,
        existing_provider_arn=existing_provider_arn,
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    template = assertions.Template.from_stack(stack)
    return app, stack, template


def test_github_oidc_stack_creates_provider_and_role() -> None:
    """GitHub OIDC provider and deploy role must be synthesized with strict trust policy."""
    _, _, template = _create_oidc_stack(environment_name="dev")

    template.has_resource_properties(
        "Custom::AWSCDKOpenIdConnectProvider",
        {
            "Url": GITHUB_OIDC_URL,
            "ClientIDList": [GITHUB_OIDC_CLIENT_ID],
            "ThumbprintList": GITHUB_OIDC_THUMBPRINTS,
        },
    )

    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "RoleName": "cloudguard-github-deploy-dev",
            "Description": "Least-privilege GitHub Actions deployment role for CloudGuard AI",
            "MaxSessionDuration": 3600,
            "AssumeRolePolicyDocument": {
                "Statement": [
                    {
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Effect": "Allow",
                        "Condition": {
                            "StringEquals": {
                                "token.actions.githubusercontent.com:aud": GITHUB_OIDC_CLIENT_ID,
                                "token.actions.githubusercontent.com:sub": (
                                    "repo:aaradhyapartap/cloudguard-ai:environment:dev"
                                ),
                            },
                        },
                        "Principal": {
                            "Federated": assertions.Match.any_value(),
                        },
                    }
                ]
            },
        },
    )


def test_github_oidc_stack_trust_policy_rejects_untrusted_subjects() -> None:
    """Trust policy must strictly enforce exact environment subject without wildcards."""
    _, _, template = _create_oidc_stack(environment_name="prod")

    template_json = template.to_json()
    role = next(
        r
        for r in template_json.get("Resources", {}).values()
        if r.get("Type") == "AWS::IAM::Role"
        and r.get("Properties", {}).get("RoleName") == "cloudguard-github-deploy-prod"
    )
    assume_doc = role["Properties"]["AssumeRolePolicyDocument"]
    stmt = assume_doc["Statement"][0]
    string_equals = stmt["Condition"]["StringEquals"]
    actual_sub = string_equals["token.actions.githubusercontent.com:sub"]

    assert actual_sub == "repo:aaradhyapartap/cloudguard-ai:environment:prod"
    # Negative assertions: verify exact equality check prevents cross-environment or PR assumption
    assert actual_sub != "repo:aaradhyapartap/cloudguard-ai:environment:dev"
    assert actual_sub != "repo:aaradhyapartap/cloudguard-ai:*"
    assert actual_sub != "repo:aaradhyapartap/cloudguard-ai:pull_request"
    assert "StringLike" not in stmt["Condition"]


def test_github_oidc_stack_least_privilege_policies() -> None:
    """The deploy role must have scoped policies, no data-plane vector access, and no wildcards."""
    _, _, template = _create_oidc_stack(cdk_qualifier="customqual")

    template_json = template.to_json()
    deploy_roles = [
        r
        for r in template_json.get("Resources", {}).values()
        if r.get("Type") == "AWS::IAM::Role"
        and r.get("Properties", {}).get("RoleName") == "cloudguard-github-deploy-dev"
    ]
    assert len(deploy_roles) == 1
    role_props = deploy_roles[0]["Properties"]

    # Must NOT have any ManagedPolicyArns like AdministratorAccess
    assert "ManagedPolicyArns" not in role_props

    policies = [
        p
        for p in template_json.get("Resources", {}).values()
        if p.get("Type") == "AWS::IAM::Policy"
    ]
    assert len(policies) >= 1

    all_statements: list[dict[str, object]] = []
    for policy in policies:
        statements = policy.get("Properties", {}).get("PolicyDocument", {}).get("Statement", [])
        all_statements.extend(statements)

    # Check that no statement grants wildcard Action "*"
    for stmt in all_statements:
        action = stmt.get("Action")
        assert action != "*", "Deploy role must not contain wildcard Action '*'"
        if isinstance(action, list):
            assert "*" not in action, "Deploy role must not contain wildcard Action '*'"

    statement_map: dict[str, dict[str, object]] = {
        str(stmt.get("Sid")): stmt for stmt in all_statements if "Sid" in stmt
    }

    # 1. Verify bootstrap roles scoping
    assert "AssumeCdkBootstrapRoles" in statement_map
    bootstrap_stmt = statement_map["AssumeCdkBootstrapRoles"]
    bootstrap_resources = bootstrap_stmt.get("Resource", [])
    assert isinstance(bootstrap_resources, list)
    for r in bootstrap_resources:
        assert "customqual" in str(r), f"Expected custom qualifier in resource {r}"
        assert "cdk-*-" not in str(r), f"CDK qualifier must not be wildcarded: {r}"

    # 2. Verify S3 Vectors permissions: control plane only, no data writes
    assert "S3VectorsControlPlaneInspection" in statement_map
    vector_stmt = statement_map["S3VectorsControlPlaneInspection"]
    vector_actions = vector_stmt.get("Action", [])
    assert isinstance(vector_actions, list)
    assert "s3vectors:GetVectorBucket" in vector_actions
    assert "s3vectors:ListIndexes" in vector_actions
    # Negative assertions: data-plane operations strictly forbidden to deployment role
    assert "s3vectors:PutVectors" not in vector_actions
    assert "s3vectors:QueryVectors" not in vector_actions
    assert "s3vectors:DeleteVectors" not in vector_actions

    # 3. Verify resource-scoped read actions are not wildcarded
    assert "DeploymentDynamoDBReadValidation" in statement_map
    ddb_res = statement_map["DeploymentDynamoDBReadValidation"].get("Resource")
    assert ddb_res != "*" and "cloudguard-*" in str(ddb_res)

    assert "DeploymentLambdaReadValidation" in statement_map
    lambda_res = statement_map["DeploymentLambdaReadValidation"].get("Resource")
    assert lambda_res != "*" and "cloudguard-*" in str(lambda_res)

    assert "DeploymentStatesReadValidation" in statement_map
    states_res = statement_map["DeploymentStatesReadValidation"].get("Resource")
    assert states_res != "*" and "cloudguard-*" in str(states_res)

    assert "DeploymentS3ReadValidation" in statement_map
    s3_res = statement_map["DeploymentS3ReadValidation"].get("Resource")
    assert s3_res != "*" and "cloudguard-*" in str(s3_res)


def test_github_oidc_stack_with_existing_provider() -> None:
    """When existing_provider_arn is provided, an OIDCProvider resource must NOT be created."""
    existing_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    _, _, template = _create_oidc_stack(existing_provider_arn=existing_arn)

    template.resource_count_is("Custom::AWSCDKOpenIdConnectProvider", 0)
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "RoleName": "cloudguard-github-deploy-dev",
            "AssumeRolePolicyDocument": {
                "Statement": [
                    {
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Effect": "Allow",
                        "Principal": {
                            "Federated": existing_arn,
                        },
                    }
                ]
            },
        },
    )


def test_github_oidc_stack_cfn_outputs() -> None:
    """Stack must export DeployRoleArn and OIDCProviderArn."""
    _, _, template = _create_oidc_stack()

    template.has_output(
        "DeployRoleArn",
        {
            "Description": "IAM Role ARN for GitHub Actions AWS federation",
            "Export": {"Name": "CloudGuardGitHubDeployRoleArn-dev"},
        },
    )
    template.has_output(
        "OIDCProviderArn",
        {
            "Description": "GitHub OIDC Provider ARN",
            "Export": {"Name": "CloudGuardGitHubOIDCProviderArn-dev"},
        },
    )


def test_ingestion_stack_grants_s3_vectors_when_arn_provided() -> None:
    """Ingestion processing Lambda must receive S3 Vectors put/get/list/delete permissions."""
    app = cdk.App()
    vector_arn = "arn:aws:s3vectors:us-east-1:123456789012:index/cloudguard-chunks-dev"
    stack = IngestionStack(
        app,
        "TestIngestionWithVectors",
        environment_name="dev",
        aurora_cluster_arn="arn:aws:rds:us-east-1:123456789012:cluster:test-cluster",
        aurora_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",
        event_bus_name="cloudguard-events",
        admission_table_name="test-admission",
        admission_table_arn="arn:aws:dynamodb:us-east-1:123456789012:table/test-admission",
        s3_vector_index_arn=vector_arn,
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Action": [
                                    "s3vectors:GetIndex",
                                    "s3vectors:PutVectors",
                                    "s3vectors:GetVectors",
                                    "s3vectors:ListVectors",
                                    "s3vectors:DeleteVectors",
                                ],
                                "Effect": "Allow",
                                "Resource": vector_arn,
                            }
                        )
                    ]
                )
            }
        },
    )

    # Lambda environment must reflect VECTOR_STORE=s3_vectors
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "VECTOR_STORE": "s3_vectors",
                        "AWS_S3_VECTOR_INDEX_ARN": vector_arn,
                    }
                )
            }
        },
    )


def test_agent_workflow_stack_grants_s3_vectors_when_arn_provided() -> None:
    """Agent workflow research function must receive S3 Vectors query/get permissions."""
    app = cdk.App()
    vector_arn = "arn:aws:s3vectors:us-east-1:123456789012:index/cloudguard-chunks-dev"
    stack = AgentWorkflowStack(
        app,
        "TestAgentWorkflowWithVectors",
        environment_name="dev",
        aurora_cluster_arn="arn:aws:rds:us-east-1:123456789012:cluster:test-cluster",
        aurora_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        reviewer_model_id="amazon.nova-pro-v1:0",
        embedding_model_id="amazon.titan-embed-text-v2:0",
        agentic_workflows_enabled=True,
        admission_table_name="test-admission",
        admission_table_arn="arn:aws:dynamodb:us-east-1:123456789012:table/test-admission",
        s3_vector_index_arn=vector_arn,
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Action": [
                                    "s3vectors:GetIndex",
                                    "s3vectors:QueryVectors",
                                    "s3vectors:GetVectors",
                                ],
                                "Effect": "Allow",
                                "Resource": vector_arn,
                            }
                        )
                    ]
                )
            }
        },
    )

    # Research Lambda environment must reflect VECTOR_STORE=s3_vectors
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "cloudguard-agent-research-dev",
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "VECTOR_STORE": "s3_vectors",
                        "AWS_S3_VECTOR_INDEX_ARN": vector_arn,
                    }
                )
            }
        },
    )
