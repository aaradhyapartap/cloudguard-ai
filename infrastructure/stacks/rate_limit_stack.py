"""Single-region API/worker admission, separate from approval audit storage."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from constructs import Construct


class RateLimitStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, environment_name: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]
        self.table = dynamodb.Table(
            self, "Counters",
            table_name=f"cloudguard-rate-limits-{environment_name}",
            partition_key=dynamodb.Attribute(
                name="counter_key", type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at",
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            deletion_protection=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        cdk.CfnOutput(self, "RateLimitTableName", value=self.table.table_name)
        cdk.CfnOutput(self, "RateLimitTableArn", value=self.table.table_arn)

    def grant_admission(self, grantee: iam.IGrantable) -> None:
        """The API needs only conditional updates, never scans/reads/audit access."""
        grantee.grant_principal.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem"], resources=[self.table.table_arn],
            conditions={"ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["api:v1:*"]}},
        ))


def grant_worker_admission(grantee: iam.IGrantable, *, table_arn: str) -> None:
    """Separate worker-rate updates and owner-checked slot writes from API counters."""
    for actions, prefix in (
        (["dynamodb:UpdateItem"], "worker:v1:*"),
        (["dynamodb:PutItem", "dynamodb:DeleteItem"], "slot:v1:*"),
    ):
        grantee.grant_principal.add_to_principal_policy(iam.PolicyStatement(
            actions=actions, resources=[table_arn],
            conditions={"ForAllValues:StringLike": {"dynamodb:LeadingKeys": [prefix]}},
        ))
