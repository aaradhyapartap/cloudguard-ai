"""Shared single-region API quota counters, separate from approval audit storage."""

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

    def grant_admission(self, grantee: iam.IGrantable) -> iam.Grant:
        """The API needs only conditional updates, never scans/reads/audit access."""
        return self.table.grant(grantee, "dynamodb:UpdateItem")
