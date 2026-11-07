"""Phase 7 deterministic agent workflow with human approval.

The workflow remains application-owned and fixed:

    Research -> Risk -> Reviewer
        Reviewer FAIL -> terminal failure
        Reviewer PASS -> human Approval wait
            rejected -> terminal non-executing success
            approved / modified -> bounded approved-action execution

Agents cannot choose the next state dynamically and cannot resume the
Step Functions execution. Only the trusted server-side callback path holds
and uses the task token.
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
    aws_stepfunctions as sfn,
)
from aws_cdk import (
    aws_stepfunctions_tasks as sfn_tasks,
)
from constructs import Construct

from stacks.rate_limit_stack import grant_worker_admission


class AgentWorkflowStack(cdk.Stack):
    """Fixed Research -> Risk -> Reviewer -> human Approval workflow."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        environment_name: str,
        aurora_cluster_arn: str,
        aurora_secret_arn: str,
        chat_model_id: str,
        reviewer_model_id: str,
        embedding_model_id: str,
        agentic_workflows_enabled: bool,
        admission_table_name: str,
        admission_table_arn: str,
        s3_vector_index_arn: str | None = None,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        feature_flag = "true" if agentic_workflows_enabled else "false"

        common_environment = {
            "ENVIRONMENT": environment_name,
            "LLM_PROVIDER": "bedrock",
            "ENABLE_AGENTIC_WORKFLOWS": feature_flag,
        }
        if guardrail_id:
            common_environment["BEDROCK_GUARDRAIL_ID"] = guardrail_id
        if guardrail_version:
            common_environment["BEDROCK_GUARDRAIL_VERSION"] = guardrail_version

        research_env = {
            **common_environment,
            "VECTOR_STORE": "s3_vectors" if s3_vector_index_arn else "pgvector",
            "AWS_AURORA_CLUSTER_ARN": aurora_cluster_arn,
            "AWS_AURORA_SECRET_ARN": aurora_secret_arn,
            "DB_NAME": "cloudguard",
            "BEDROCK_CHAT_MODEL": chat_model_id,
            "BEDROCK_EMBEDDING_MODEL": embedding_model_id,
            "RATE_LIMIT_TABLE_NAME": admission_table_name,
        }
        if s3_vector_index_arn:
            research_env["AWS_S3_VECTOR_INDEX_ARN"] = s3_vector_index_arn
            import re
            arn_match = re.match(
                r"^arn:aws:s3vectors:[a-z0-9-]+:[0-9]{12}:bucket/([a-zA-Z0-9.-]+)/index/([a-zA-Z0-9_-]+)$",
                s3_vector_index_arn,
            )
            if arn_match:
                research_env["AWS_S3_VECTOR_BUCKET_NAME"] = arn_match.group(1)
                research_env["AWS_S3_VECTOR_INDEX_NAME"] = arn_match.group(2)

        self.research_function = lambda_.DockerImageFunction(
            self,
            "ResearchFunction",
            function_name=f"cloudguard-agent-research-{environment_name}",
            code=lambda_.DockerImageCode.from_image_asset(
                "../backend",
                cmd=["app.deployed_research_agent_worker.handler"],
            ),
            timeout=cdk.Duration.minutes(2),
            memory_size=512,
            reserved_concurrent_executions=2,
            environment=research_env,
        )

        self.risk_function = lambda_.DockerImageFunction(
            self,
            "RiskFunction",
            function_name=f"cloudguard-agent-risk-{environment_name}",
            code=lambda_.DockerImageCode.from_image_asset(
                "../backend",
                cmd=["app.deployed_risk_agent_worker.handler"],
            ),
            timeout=cdk.Duration.minutes(2),
            memory_size=512,
            reserved_concurrent_executions=2,
            environment={
                **common_environment,
                "BEDROCK_CHAT_MODEL": chat_model_id,
                "RATE_LIMIT_TABLE_NAME": admission_table_name,
            },
        )

        self.reviewer_function = lambda_.DockerImageFunction(
            self,
            "ReviewerFunction",
            function_name=f"cloudguard-agent-reviewer-{environment_name}",
            code=lambda_.DockerImageCode.from_image_asset(
                "../backend",
                cmd=["app.deployed_reviewer_agent_worker.handler"],
            ),
            timeout=cdk.Duration.minutes(2),
            memory_size=512,
            reserved_concurrent_executions=2,
            environment={
                **common_environment,
                "BEDROCK_JUDGE_MODEL": reviewer_model_id,
                "RATE_LIMIT_TABLE_NAME": admission_table_name,
            },
        )

        for function in (self.research_function, self.risk_function, self.reviewer_function):
            grant_worker_admission(function, table_arn=admission_table_arn)

        self.approval_creation_function = lambda_.DockerImageFunction(
            self,
            "ApprovalCreationFunction",
            function_name=f"cloudguard-agent-approval-{environment_name}",
            code=lambda_.DockerImageCode.from_image_asset(
                "../backend",
                cmd=["app.deployed_approval_creation_worker.handler"],
            ),
            timeout=cdk.Duration.minutes(1),
            memory_size=512,
            environment={
                **common_environment,
                "AWS_AURORA_CLUSTER_ARN": aurora_cluster_arn,
                "AWS_AURORA_SECRET_ARN": aurora_secret_arn,
                "DB_NAME": "cloudguard",
            },
        )

        self.approval_callback_recovery_function = (
            lambda_.DockerImageFunction(
                self,
                "ApprovalCallbackRecoveryFunction",
                function_name=(
                    f"cloudguard-approval-callback-recovery-"
                    f"{environment_name}"
                ),
                code=lambda_.DockerImageCode.from_image_asset(
                    "../backend",
                    cmd=[
                        "app.deployed_approval_callback_recovery_worker.handler"
                    ],
                ),
                timeout=cdk.Duration.minutes(2),
                memory_size=512,
                environment={
                    **common_environment,
                    "AWS_AURORA_CLUSTER_ARN": aurora_cluster_arn,
                    "AWS_AURORA_SECRET_ARN": aurora_secret_arn,
                    "DB_NAME": "cloudguard",
                },
            )
        )

        self.approved_action_execution_function = (
            lambda_.DockerImageFunction(
                self,
                "ApprovedActionExecutionFunction",
                function_name=(
                    f"cloudguard-approved-action-execution-"
                    f"{environment_name}"
                ),
                code=lambda_.DockerImageCode.from_image_asset(
                    "../backend",
                    cmd=[
                        "app.deployed_approved_action_execution_worker.handler"
                    ],
                ),
                timeout=cdk.Duration.minutes(2),
                memory_size=512,
                environment={
                    **common_environment,
                    "AWS_AURORA_CLUSTER_ARN": aurora_cluster_arn,
                    "AWS_AURORA_SECRET_ARN": aurora_secret_arn,
                    "DB_NAME": "cloudguard",
                },
            )
        )

        research_model_arn = self.format_arn(
            service="bedrock",
            resource="foundation-model",
            resource_name=chat_model_id,
            account="",
        )
        embedding_model_arn = self.format_arn(
            service="bedrock",
            resource="foundation-model",
            resource_name=embedding_model_id,
            account="",
        )
        reviewer_model_arn = self.format_arn(
            service="bedrock",
            resource="foundation-model",
            resource_name=reviewer_model_id,
            account="",
        )

        self.research_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel"],
                resources=[research_model_arn, embedding_model_arn],
            )
        )
        self.research_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["rds-data:ExecuteStatement"],
                resources=[aurora_cluster_arn],
            )
        )
        self.research_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[aurora_secret_arn],
            )
        )
        if s3_vector_index_arn:
            self.research_function.add_to_role_policy(
                iam.PolicyStatement(
                    sid="S3VectorsResearchAccess",
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "s3vectors:GetIndex",
                        "s3vectors:QueryVectors",
                        "s3vectors:GetVectors",
                    ],
                    resources=[s3_vector_index_arn],
                )
            )

        self.risk_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel"],
                resources=[research_model_arn],
            )
        )

        self.reviewer_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel"],
                resources=[reviewer_model_arn],
            )
        )

        if guardrail_id:
            guardrail_arn = f"arn:aws:bedrock:{self.region}:{self.account}:guardrail/{guardrail_id}"
            apply_guardrail_statement = iam.PolicyStatement(
                sid="BedrockApplyGuardrailAccess",
                effect=iam.Effect.ALLOW,
                actions=["bedrock:ApplyGuardrail"],
                resources=[guardrail_arn],
            )
            self.research_function.add_to_role_policy(apply_guardrail_statement)
            self.risk_function.add_to_role_policy(apply_guardrail_statement)
            self.reviewer_function.add_to_role_policy(apply_guardrail_statement)

        self.approval_creation_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "rds-data:BeginTransaction",
                    "rds-data:ExecuteStatement",
                    "rds-data:CommitTransaction",
                    "rds-data:RollbackTransaction",
                ],
                resources=[aurora_cluster_arn],
            )
        )
        self.approval_creation_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[aurora_secret_arn],
            )
        )

        self.approval_callback_recovery_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "rds-data:BeginTransaction",
                    "rds-data:ExecuteStatement",
                    "rds-data:CommitTransaction",
                    "rds-data:RollbackTransaction",
                ],
                resources=[aurora_cluster_arn],
            )
        )
        self.approval_callback_recovery_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[aurora_secret_arn],
            )
        )
        self.approval_callback_recovery_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["states:SendTaskSuccess"],
                # Callback task tokens do not map to a resource-level ARN.
                resources=["*"],
            )
        )

        self.approval_callback_recovery_rule = events.Rule(
            self,
            "ApprovalCallbackRecoverySchedule",
            schedule=events.Schedule.rate(
                cdk.Duration.minutes(1)
            ),
        )
        self.approval_callback_recovery_rule.add_target(
            events_targets.LambdaFunction(
                self.approval_callback_recovery_function
            )
        )

        self.approved_action_execution_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "rds-data:BeginTransaction",
                    "rds-data:ExecuteStatement",
                    "rds-data:CommitTransaction",
                    "rds-data:RollbackTransaction",
                ],
                resources=[aurora_cluster_arn],
            )
        )
        self.approved_action_execution_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[aurora_secret_arn],
            )
        )

        normalize_input = sfn.Pass(
            self,
            "NormalizeWorkflowInput",
            parameters={
                "execution_id": sfn.JsonPath.uuid(),
                "correlation_id": sfn.JsonPath.string_at("$.correlation_id"),
                "question": sfn.JsonPath.string_at("$.question"),
                "principal": sfn.JsonPath.object_at("$.principal"),
            },
        )

        research = sfn_tasks.LambdaInvoke(
            self,
            "ResearchAgent",
            retry_on_service_exceptions=False,
            lambda_function=self.research_function,
            payload_response_only=True,
            payload=sfn.TaskInput.from_json_path_at("$"),
        )

        risk = sfn_tasks.LambdaInvoke(
            self,
            "RiskAgent",
            retry_on_service_exceptions=False,
            lambda_function=self.risk_function,
            payload_response_only=True,
            payload=sfn.TaskInput.from_json_path_at("$"),
        )

        reviewer = sfn_tasks.LambdaInvoke(
            self,
            "ReviewerAgent",
            retry_on_service_exceptions=False,
            lambda_function=self.reviewer_function,
            payload_response_only=True,
            payload=sfn.TaskInput.from_json_path_at("$"),
        )

        approval_wait = sfn_tasks.LambdaInvoke(
            self,
            "WaitForManagerApproval",
            retry_on_service_exceptions=False,
            lambda_function=self.approval_creation_function,
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload=sfn.TaskInput.from_object(
                {
                    "execution_id": sfn.JsonPath.string_at("$.execution_id"),
                    "correlation_id": sfn.JsonPath.string_at("$.correlation_id"),
                    "question": sfn.JsonPath.string_at("$.question"),
                    "principal": sfn.JsonPath.object_at("$.principal"),
                    "research": sfn.JsonPath.object_at("$.research"),
                    "reviewer": sfn.JsonPath.object_at("$.reviewer"),
                    "task_token": sfn.JsonPath.task_token,
                }
            ),
            result_path="$.approval_decision",
        )

        execute_approved_action = sfn_tasks.LambdaInvoke(
            self,
            "ExecuteApprovedAction",
            retry_on_service_exceptions=False,
            lambda_function=self.approved_action_execution_function,
            payload_response_only=True,
            payload=sfn.TaskInput.from_object(
                {
                    "execution_id": sfn.JsonPath.string_at(
                        "$.execution_id"
                    ),
                    "principal": sfn.JsonPath.object_at(
                        "$.principal"
                    ),
                }
            ),
            result_path="$.execution_result",
        )

        # Retry only admission throttles (the function has not started), never
        # uncertain invocation outcomes or application/model/side-effect errors.
        for task in (research, risk, reviewer, approval_wait, execute_approved_action):
            task.add_retry(
                errors=["Lambda.TooManyRequestsException"],
                interval=cdk.Duration.seconds(2), backoff_rate=2, max_attempts=2,
            )

        workflow_executed = sfn.Succeed(
            self,
            "WorkflowExecuted",
            comment=(
                "The exact human-approved bounded action "
                "completed successfully."
            ),
        )

        workflow_rejected = sfn.Succeed(
            self,
            "WorkflowRejected",
            comment="Manager rejected the proposed action; no action is executed.",
        )

        reviewer_failed = sfn.Fail(
            self,
            "ReviewerFailed",
            error="ReviewerRejectedWorkflow",
            cause="Reviewer returned FAIL.",
        )

        agent_execution_failed = sfn.Fail(
            self,
            "AgentExecutionFailed",
            error="AgentExecutionFailed",
            cause="A bounded agent task failed.",
        )

        approval_wait_failed = sfn.Fail(
            self,
            "ApprovalWaitFailed",
            error="ApprovalWaitFailed",
            cause="The human approval wait task failed.",
        )

        approved_action_execution_failed = sfn.Fail(
            self,
            "ApprovedActionExecutionFailed",
            error="ApprovedActionExecutionFailed",
            cause=(
                "The persisted human-approved bounded action "
                "could not be executed."
            ),
        )

        for task in (research, risk, reviewer):
            task.add_catch(
                agent_execution_failed,
                errors=["States.ALL"],
                result_path="$.ErrorDetails",
            )

        approval_wait.add_catch(
            approval_wait_failed,
            errors=["States.ALL"],
            result_path="$.ErrorDetails",
        )

        execute_approved_action.add_catch(
            approved_action_execution_failed,
            errors=["States.ALL"],
            result_path="$.ErrorDetails",
        )
        execute_approved_action.next(
            workflow_executed
        )

        approval_decision = sfn.Choice(
            self,
            "ApprovalDecision",
        )
        approval_decision.when(
            sfn.Condition.string_equals(
                "$.approval_decision.decision",
                "rejected",
            ),
            workflow_rejected,
        )
        approval_decision.when(
            sfn.Condition.or_(
                sfn.Condition.string_equals(
                    "$.approval_decision.decision",
                    "approved",
                ),
                sfn.Condition.string_equals(
                    "$.approval_decision.decision",
                    "modified",
                ),
            ),
            execute_approved_action,
        )
        approval_decision.otherwise(approval_wait_failed)

        approval_wait.next(approval_decision)

        reviewer_decision = sfn.Choice(
            self,
            "ReviewerDecision",
        )
        reviewer_decision.when(
            sfn.Condition.string_equals("$.reviewer.decision", "PASS"),
            approval_wait,
        )
        reviewer_decision.otherwise(reviewer_failed)

        definition = (
            normalize_input.next(research)
            .next(risk)
            .next(reviewer)
            .next(reviewer_decision)
        )

        self.state_machine = sfn.StateMachine(
            self,
            "AgentWorkflowStateMachine",
            state_machine_name=f"cloudguard-agent-workflow-{environment_name}",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            state_machine_type=sfn.StateMachineType.STANDARD,
            timeout=cdk.Duration.days(365),
            tracing_enabled=True,
        )

        cfn_sm = self.state_machine.node.default_child
        assert isinstance(cfn_sm, sfn.CfnStateMachine)
        # Execution payloads can contain evidence and internal task capabilities.
        # This disables CloudWatch payload copies, not Standard execution history.
        cfn_sm.add_property_override(
            "LoggingConfiguration", {"Level": "OFF", "IncludeExecutionData": False}
        )
        cfn_sm.add_property_override(
            "EncryptionConfiguration",
            {"Type": "AWS_OWNED_KEY"},
        )

        cdk.CfnOutput(
            self,
            "AgentWorkflowStateMachineArn",
            value=self.state_machine.state_machine_arn,
            description="Phase 7 human-approval agent workflow state machine ARN",
        )
