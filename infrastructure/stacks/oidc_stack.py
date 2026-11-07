"""GitHub Actions OIDC deployment role and least-privilege policies.

Enforces secure, keyless deployment from GitHub Actions into AWS using OIDC federation
(sts:AssumeRoleWithWebIdentity) strictly scoped to the repository and target environment.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_CLIENT_ID = "sts.amazonaws.com"
# Official GitHub Actions OIDC thumbprints
GITHUB_OIDC_THUMBPRINTS = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c5824a9fd0c1e3cd858022396929f6b39923057",
]


class GitHubOIDCStack(cdk.Stack):
    """Stack for GitHub Actions OpenID Connect federation and deployment role."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        environment_name: str,
        github_owner: str = "aaradhyapartap",
        github_repo: str = "cloudguard-ai",
        cdk_qualifier: str = "hnb659fds",
        existing_provider_arn: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        # 1. Resolve or create GitHub OIDC Provider
        if existing_provider_arn:
            provider = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
                self, "ImportedGitHubOIDCProvider", existing_provider_arn
            )
        else:
            provider = iam.OpenIdConnectProvider(
                self,
                "GitHubOIDCProvider",
                url=GITHUB_OIDC_URL,
                client_ids=[GITHUB_OIDC_CLIENT_ID],
                thumbprints=GITHUB_OIDC_THUMBPRINTS,
            )

        # 2. Strict Subject Condition for GitHub Actions Environment
        # In GitHub Actions, jobs specifying `environment: <env>` have sub:
        # repo:<owner>/<repo>:environment:<env>
        # This strictly binds the deployment role to the exact target environment.
        repo_sub_condition = f"repo:{github_owner}/{github_repo}:environment:{environment_name}"

        # 3. GitHub Deploy IAM Role
        self.deploy_role = iam.Role(
            self,
            "GitHubDeployRole",
            role_name=f"cloudguard-github-deploy-{environment_name}",
            description="Least-privilege GitHub Actions deployment role for CloudGuard AI",
            assumed_by=iam.FederatedPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": GITHUB_OIDC_CLIENT_ID,
                        "token.actions.githubusercontent.com:sub": repo_sub_condition,
                    },
                },
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
            max_session_duration=cdk.Duration.hours(1),
        )

        # 4. Least-Privilege Policy: Assume CDK v2 Bootstrap Roles
        # Strictly scoped to the configured qualifier, account, and region (no wildcards)
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                sid="AssumeCdkBootstrapRoles",
                effect=iam.Effect.ALLOW,
                actions=["sts:AssumeRole"],
                resources=[
                    (
                        f"arn:aws:iam::{self.account}:role/"
                        f"cdk-{cdk_qualifier}-lookup-role-{self.account}-{self.region}"
                    ),
                    (
                        f"arn:aws:iam::{self.account}:role/"
                        f"cdk-{cdk_qualifier}-deploy-role-{self.account}-{self.region}"
                    ),
                    (
                        f"arn:aws:iam::{self.account}:role/"
                        f"cdk-{cdk_qualifier}-file-publishing-role-{self.account}-{self.region}"
                    ),
                    (
                        f"arn:aws:iam::{self.account}:role/"
                        f"cdk-{cdk_qualifier}-image-publishing-role-{self.account}-{self.region}"
                    ),
                ],
            )
        )

        # 5. Least-Privilege Policy: CloudFormation Read/Inspect for CloudGuard Stacks
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudFormationReadStatus",
                effect=iam.Effect.ALLOW,
                actions=[
                    "cloudformation:DescribeStacks",
                    "cloudformation:DescribeStackEvents",
                    "cloudformation:GetTemplate",
                    "cloudformation:ListStacks",
                ],
                resources=[
                    f"arn:aws:cloudformation:{self.region}:{self.account}:stack/CloudGuard*/*"
                ],
            )
        )

        # 6. Least-Privilege Policy: S3 Vectors Control-Plane Inspection
        # Deployment role only inspects bucket/index existence. Data-plane actions
        # (PutVectors, QueryVectors, DeleteVectors) are strictly denied to deployment role.
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                sid="S3VectorsControlPlaneInspection",
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3vectors:GetVectorBucket",
                    "s3vectors:ListVectorBuckets",
                    "s3vectors:GetIndex",
                    "s3vectors:ListIndexes",
                ],
                resources=[
                    f"arn:aws:s3vectors:{self.region}:{self.account}:bucket/*",
                    f"arn:aws:s3vectors:{self.region}:{self.account}:bucket/*/index/*",
                ],
            )
        )

        # 7. Least-Privilege Policy: Resource-Scoped Read Validation
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                sid="DeploymentDynamoDBReadValidation",
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:DescribeTable"],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cloudguard-*"
                ],
            )
        )
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                sid="DeploymentLambdaReadValidation",
                effect=iam.Effect.ALLOW,
                actions=["lambda:GetFunction"],
                resources=[
                    f"arn:aws:lambda:{self.region}:{self.account}:function:cloudguard-*"
                ],
            )
        )
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                sid="DeploymentStatesReadValidation",
                effect=iam.Effect.ALLOW,
                actions=["states:DescribeStateMachine"],
                resources=[
                    f"arn:aws:states:{self.region}:{self.account}:stateMachine:cloudguard-*"
                ],
            )
        )
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                sid="DeploymentS3ReadValidation",
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetBucketLocation",
                    "s3:ListBucket",
                ],
                resources=["arn:aws:s3:::cloudguard-*"],
            )
        )
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                sid="DeploymentGlobalReadValidation",
                effect=iam.Effect.ALLOW,
                actions=[
                    "cognito-idp:DescribeUserPool",
                    "cognito-idp:DescribeUserPoolClient",
                    "cloudwatch:DescribeAlarms",
                ],
                resources=["*"],
            )
        )

        # 8. Stack Outputs
        cdk.CfnOutput(
            self,
            "DeployRoleArn",
            value=self.deploy_role.role_arn,
            description="IAM Role ARN for GitHub Actions AWS federation",
            export_name=f"CloudGuardGitHubDeployRoleArn-{environment_name}",
        )
        cdk.CfnOutput(
            self,
            "OIDCProviderArn",
            value=provider.open_id_connect_provider_arn,
            description="GitHub OIDC Provider ARN",
            export_name=f"CloudGuardGitHubOIDCProviderArn-{environment_name}",
        )
