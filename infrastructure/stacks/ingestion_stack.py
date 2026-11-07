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
)
from aws_cdk import (
    aws_events_targets as events_targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)
from aws_cdk import (
    aws_stepfunctions_tasks as sfn_tasks,
)
from constructs import Construct

from stacks.rate_limit_stack import grant_worker_admission


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
        admission_table_name: str,
        admission_table_arn: str,
        s3_vector_index_arn: str | None = None,
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
        processing_env = {
            "RATE_LIMIT_TABLE_NAME": admission_table_name,
            "ENVIRONMENT": environment_name,
            "AWS_AURORA_CLUSTER_ARN": aurora_cluster_arn,
            "AWS_AURORA_SECRET_ARN": aurora_secret_arn,
            "DB_NAME": "cloudguard",
            "DOCUMENT_STORE": "s3",
            "AWS_DOCUMENTS_BUCKET": self.documents_bucket.bucket_name,
            "EVENT_PUBLISHER": "eventbridge",
            "AWS_EVENT_BUS_NAME": self.event_bus.event_bus_name,
            "VECTOR_STORE": "s3_vectors" if s3_vector_index_arn else "pgvector",
        }
        if s3_vector_index_arn:
            processing_env["AWS_S3_VECTOR_INDEX_ARN"] = s3_vector_index_arn
            import re
            arn_match = re.match(
                r"^arn:aws:s3vectors:[a-z0-9-]+:[0-9]{12}:bucket/([a-zA-Z0-9.-]+)/index/([a-zA-Z0-9_-]+)$",
                s3_vector_index_arn,
            )
            if arn_match:
                processing_env["AWS_S3_VECTOR_BUCKET_NAME"] = arn_match.group(1)
                processing_env["AWS_S3_VECTOR_INDEX_NAME"] = arn_match.group(2)

        self.processing_function = lambda_.DockerImageFunction(
            self,
            "ProcessingFunction",
            function_name=f"cloudguard-document-processor-{environment_name}",
            code=lambda_.DockerImageCode.from_image_asset(
                "../backend",
                cmd=["app.deployed_document_worker.handler"],
            ),
            timeout=cdk.Duration.minutes(5),
            reserved_concurrent_executions=2,
            memory_size=512,
            environment=processing_env,
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

        # S3 Vectors permissions if vector index ARN is configured
        if s3_vector_index_arn:
            self.processing_function.add_to_role_policy(
                iam.PolicyStatement(
                    sid="S3VectorsIngestionAccess",
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "s3vectors:GetIndex",
                        "s3vectors:PutVectors",
                        "s3vectors:GetVectors",
                        "s3vectors:ListVectors",
                        "s3vectors:DeleteVectors",
                    ],
                    resources=[s3_vector_index_arn],
                )
            )

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

        grant_worker_admission(self.processing_function, table_arn=admission_table_arn)

        process_document = sfn_tasks.LambdaInvoke(
            self,
            "ProcessDocument",
            retry_on_service_exceptions=False,
            lambda_function=self.processing_function,
            # Pass the normalized payload directly, not wrapped in a task token
            payload_response_only=True,
            payload=sfn.TaskInput.from_json_path_at("$"),
        )

        process_document.add_retry(
            errors=["Lambda.TooManyRequestsException"],
            interval=cdk.Duration.seconds(2), backoff_rate=2, max_attempts=2,
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
            "LoggingConfiguration", {"Level": "OFF", "IncludeExecutionData": False}
        )
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
                retry_attempts=2,
                max_event_age=cdk.Duration.hours(1),
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
