"""Phase 3 document-ingestion infrastructure.

Production ingestion flow:

    S3 ObjectCreated
    -> EventBridge (default bus)
    -> Step Functions Standard workflow
    -> Processing Lambda (outside VPC, Data API)

The S3 key layout is ``org/{organization_id}/documents/{document_id}/{filename}``.
Step Functions uses intrinsic functions to extract the two UUIDs from the key
and passes them as a normalized payload to the processing Lambda.

Failed executions are routed to an SQS dead-letter queue.
"""

from __future__ import annotations


import aws_cdk as cdk
from aws_cdk import (
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_sqs as sqs,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as sfn_tasks,
)
from constructs import Construct


class IngestionStack(cdk.Stack):
    """S3 bucket, EventBridge rule, Step Functions workflow, and processing Lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        environment_name: str,
        aurora_cluster_arn: str,
        aurora_secret_arn: str,
        event_bus_name: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        is_ephemeral = environment_name == "dev"

        # ------------------------------------------------------------------ S3
        self.documents_bucket = s3.Bucket(
            self,
            "DocumentsBucket",
            bucket_name=f"cloudguard-documents-{environment_name}-{self.account}",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            event_bridge_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=(
                cdk.RemovalPolicy.DESTROY if is_ephemeral else cdk.RemovalPolicy.RETAIN
            ),
            auto_delete_objects=is_ephemeral,
        )

        # ------------------------------------------------------------------ SQS DLQ
        self.ingestion_dlq = sqs.Queue(
            self,
            "IngestionDLQ",
            queue_name=f"cloudguard-ingestion-dlq-{environment_name}",
            retention_period=cdk.Duration.days(14),
            removal_policy=(
                cdk.RemovalPolicy.DESTROY if is_ephemeral else cdk.RemovalPolicy.RETAIN
            ),
        )

        # -------------------------------------------------- EventBridge Bus
        self.event_bus = events.EventBus(
            self,
            "EventBus",
            event_bus_name=event_bus_name,
        )

        # ------------------------------------------------------------------ Lambda
        self.processing_function = lambda_.DockerImageFunction(
            self,
            "ProcessingFunction",
            function_name=f"cloudguard-document-processor-{environment_name}",
            code=lambda_.DockerImageCode.from_image_asset(
                "../backend",
                cmd=["app.deployed_document_worker.handler"],
            ),
            timeout=cdk.Duration.minutes(5),
            memory_size=512,
            environment={
                "ENVIRONMENT": environment_name,
                "AWS_AURORA_CLUSTER_ARN": aurora_cluster_arn,
                "AWS_AURORA_SECRET_ARN": aurora_secret_arn,
                "DB_NAME": "cloudguard",
                "DOCUMENT_STORE": "s3",
                "AWS_DOCUMENTS_BUCKET": self.documents_bucket.bucket_name,
                "EVENT_PUBLISHER": "eventbridge",
                "AWS_EVENT_BUS_NAME": self.event_bus.event_bus_name,
            },
            # Deliberately NO vpc — ADR-0008.
        )

        # S3 read access for document retrieval
        self.documents_bucket.grant_read(self.processing_function)

        # Data API permissions (scoped to cluster)
        self.processing_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "rds-data:ExecuteStatement",
                    "rds-data:BatchExecuteStatement",
                    "rds-data:BeginTransaction",
                    "rds-data:CommitTransaction",
                    "rds-data:RollbackTransaction",
                ],
                resources=[aurora_cluster_arn],
            )
        )

        # Secrets Manager access for Aurora credentials
        self.processing_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[aurora_secret_arn],
            )
        )

        # EventBridge publish access for domain events (scoped to custom event bus ARN)
        self.event_bus.grant_put_events_to(self.processing_function)

        # --------------------------------------------------------- Step Functions
        #
        # The S3 key layout is:
        #   org/{organization_id}/documents/{document_id}/{filename}
        #
        # Segments when split by "/":
        #   [0] = "org"
        #   [1] = organization_id
        #   [2] = "documents"
        #   [3] = document_id
        #   [4] = filename
        #
        # Step Functions intrinsic functions extract the UUIDs without a
        # normalization Lambda.

        normalize_input = sfn.Pass(
            self,
            "NormalizeInput",
            parameters={
                "organization_id": sfn.JsonPath.array_get_item(
                    sfn.JsonPath.string_split(
                        sfn.JsonPath.string_at("$.detail.object.key"), "/"
                    ),
                    1,
                ),
                "document_id": sfn.JsonPath.array_get_item(
                    sfn.JsonPath.string_split(
                        sfn.JsonPath.string_at("$.detail.object.key"), "/"
                    ),
                    3,
                ),
            },
        )

        process_document = sfn_tasks.LambdaInvoke(
            self,
            "ProcessDocument",
            lambda_function=self.processing_function,
            # Pass the normalized payload directly, not wrapped in a task token
            payload_response_only=True,
            payload=sfn.TaskInput.from_json_path_at("$"),
        )

        send_failure_to_dlq = sfn_tasks.SqsSendMessage(
            self,
            "SendFailureToDLQ",
            queue=self.ingestion_dlq,
            message_body=sfn.TaskInput.from_object(
                {
                    "execution_id": sfn.JsonPath.string_at("$$.Execution.Id"),
                    "organization_id": sfn.JsonPath.string_at("$.organization_id"),
                    "document_id": sfn.JsonPath.string_at("$.document_id"),
                    "error": sfn.JsonPath.string_at("$.ErrorDetails.Error"),
                    "cause": sfn.JsonPath.string_at("$.ErrorDetails.Cause"),
                }
            ),
        )

        fail_state = sfn.Fail(
            self,
            "IngestionFailed",
            error="DocumentProcessingFailed",
            cause="Document processing execution failed; failure record sent to DLQ.",
        )

        send_failure_to_dlq.next(fail_state)

        process_document.add_catch(
            send_failure_to_dlq,
            errors=["States.ALL"],
            result_path="$.ErrorDetails",
        )

        definition = normalize_input.next(process_document)

        self.state_machine = sfn.StateMachine(
            self,
            "IngestionStateMachine",
            state_machine_name=f"cloudguard-ingestion-{environment_name}",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            state_machine_type=sfn.StateMachineType.STANDARD,
            timeout=cdk.Duration.minutes(15),
            tracing_enabled=True,
        )

        # Attach DLQ for failed executions.
        # CloudWatch alarm on the DLQ is a future observability task.
        cfn_sm = self.state_machine.node.default_child
        assert isinstance(cfn_sm, sfn.CfnStateMachine)
        cfn_sm.add_property_override(
            "EncryptionConfiguration",
            {"Type": "AWS_OWNED_KEY"},
        )

        # --------------------------------------------------------- EventBridge
        #
        # Match S3 ObjectCreated events on the documents bucket.
        # The default event bus receives S3 notifications when
        # event_bridge_enabled=True on the bucket.

        self.ingestion_rule = events.Rule(
            self,
            "IngestionRule",
            rule_name=f"cloudguard-ingestion-trigger-{environment_name}",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [self.documents_bucket.bucket_name]},
                },
            ),
        )

        self.ingestion_rule.add_target(
            events_targets.SfnStateMachine(
                self.state_machine,
                dead_letter_queue=self.ingestion_dlq,
            )
        )

        # ------------------------------------------------------------------ Outputs
        for key, value, description in (
            (
                "DocumentsBucketName",
                self.documents_bucket.bucket_name,
                "S3 bucket for document uploads",
            ),
            (
                "ProcessingFunctionArn",
                self.processing_function.function_arn,
                "Document processing Lambda ARN",
            ),
            (
                "StateMachineArn",
                self.state_machine.state_machine_arn,
                "Ingestion Step Functions state machine ARN",
            ),
            (
                "IngestionDLQUrl",
                self.ingestion_dlq.queue_url,
                "SQS dead-letter queue for failed ingestions",
            ),
            (
                "EventBusName",
                self.event_bus.event_bus_name,
                "Custom application EventBridge bus name",
            ),
            (
                "EventBusArn",
                self.event_bus.event_bus_arn,
                "Custom application EventBridge bus ARN",
            ),
        ):
            cdk.CfnOutput(self, key, value=value, description=description)
