"""Phase 10 Observability Stack: CloudWatch Dashboards, Operational Metrics, and Alarms.

Rules:
- Uses actual implemented AWS resources (Ingestion, AgentWorkflow, RateLimits).
- Does not invent non-existent deployed API constructs.
- Bounded metric dimensions only; no sensitive text, user IDs, or tenant IDs.
- Meaningful threshold alarms for failure detection, DLQ buildup, and capacity exhaustion.
"""

from __future__ import annotations

from typing import Any

import aws_cdk as cdk
from aws_cdk import (
    aws_cloudwatch as cw,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
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
from constructs import Construct

CLOUDWATCH_NAMESPACE = "CloudGuardAI"


class ObservabilityStack(cdk.Stack):
    """Provisions CloudWatch operational dashboards and alarms for CloudGuard AI."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        environment_name: str,
        ingestion_dlq: sqs.IQueue,
        ingestion_state_machine: sfn.IStateMachine,
        processing_function: lambda_.IFunction,
        agent_workflow_state_machine: sfn.IStateMachine,
        rate_limit_table: dynamodb.ITable,
        documents_bucket: s3.IBucket | None = None,
        research_function: lambda_.IFunction | None = None,
        risk_function: lambda_.IFunction | None = None,
        reviewer_function: lambda_.IFunction | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.alarms: list[cw.Alarm] = []

        # ---------------------------------------------------------------------
        # 1. Operational Alarms (Real Failure & Anomaly Detection)
        # ---------------------------------------------------------------------

        # Ingestion DLQ: immediate alarm on failed document processing messages
        dlq_messages_metric = ingestion_dlq.metric_approximate_number_of_messages_visible(
            period=cdk.Duration.minutes(1),
            statistic="Maximum",
        )
        self.ingestion_dlq_alarm = cw.Alarm(
            self,
            "IngestionDlqVisibleMessagesAlarm",
            alarm_name=f"cloudguard-ingestion-dlq-alarm-{environment_name}",
            alarm_description="Unprocessed document failures detected in ingestion DLQ.",
            metric=dlq_messages_metric,
            threshold=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self.alarms.append(self.ingestion_dlq_alarm)

        # Ingestion Step Function Execution Failures
        ingestion_failed_metric = ingestion_state_machine.metric_failed(
            period=cdk.Duration.minutes(1),
            statistic="Sum",
        )
        self.ingestion_failed_alarm = cw.Alarm(
            self,
            "IngestionExecutionFailedAlarm",
            alarm_name=f"cloudguard-ingestion-failed-{environment_name}",
            alarm_description="Document ingestion Step Functions execution failure.",
            metric=ingestion_failed_metric,
            threshold=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self.alarms.append(self.ingestion_failed_alarm)

        # Document Processing Lambda Errors
        processor_error_metric = processing_function.metric_errors(
            period=cdk.Duration.minutes(1),
            statistic="Sum",
        )
        self.processor_error_alarm = cw.Alarm(
            self,
            "ProcessorLambdaErrorsAlarm",
            alarm_name=f"cloudguard-processor-lambda-errors-{environment_name}",
            alarm_description="Document processing Lambda execution errors.",
            metric=processor_error_metric,
            threshold=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self.alarms.append(self.processor_error_alarm)

        # Agent Workflow Step Function Execution Failures
        workflow_failed_metric = agent_workflow_state_machine.metric_failed(
            period=cdk.Duration.minutes(1),
            statistic="Sum",
        )
        self.agent_workflow_failed_alarm = cw.Alarm(
            self,
            "AgentWorkflowExecutionFailedAlarm",
            alarm_name=f"cloudguard-agent-workflow-failed-{environment_name}",
            alarm_description="Agent workflow Step Functions execution failure.",
            metric=workflow_failed_metric,
            threshold=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self.alarms.append(self.agent_workflow_failed_alarm)

        # Agent Workflow Throttles
        workflow_throttled_metric = agent_workflow_state_machine.metric_throttled(
            period=cdk.Duration.minutes(1),
            statistic="Sum",
        )
        self.agent_workflow_throttled_alarm = cw.Alarm(
            self,
            "AgentWorkflowThrottledAlarm",
            alarm_name=f"cloudguard-agent-workflow-throttled-{environment_name}",
            alarm_description="Agent workflow Step Functions executions are throttled.",
            metric=workflow_throttled_metric,
            threshold=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self.alarms.append(self.agent_workflow_throttled_alarm)

        # Rate Limit Table System Errors across actual admission operations (UpdateItem, PutItem, DeleteItem)
        dynamo_operations = ["UpdateItem", "PutItem", "DeleteItem"]
        dynamo_op_metrics: dict[str, cw.IMetric] = {
            f"m{i}": cw.Metric(
                namespace="AWS/DynamoDB",
                metric_name="SystemErrors",
                dimensions_map={"TableName": rate_limit_table.table_name, "Operation": op},
                period=cdk.Duration.minutes(1),
                statistic="Sum",
            )
            for i, op in enumerate(dynamo_operations)
        }
        dynamo_errors_metric = cw.MathExpression(
            expression=" + ".join(dynamo_op_metrics.keys()),
            using_metrics=dynamo_op_metrics,
            period=cdk.Duration.minutes(1),
            label="RateLimitTableSystemErrors",
        )
        self.rate_limit_table_error_alarm = cw.Alarm(
            self,
            "RateLimitTableSystemErrorsAlarm",
            alarm_name=f"cloudguard-rate-limit-table-errors-{environment_name}",
            alarm_description="DynamoDB system errors on rate limit table across admission operations.",
            metric=dynamo_errors_metric,
            threshold=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self.alarms.append(self.rate_limit_table_error_alarm)

        # Custom Metric Alarm: Rate Limit & Worker Admission Denials Spike
        admission_denied_metric = cw.Metric(
            namespace=CLOUDWATCH_NAMESPACE,
            metric_name="AdmissionDenied",
            dimensions_map={"Environment": environment_name},
            period=cdk.Duration.minutes(5),
            statistic="Sum",
        )
        self.admission_denials_alarm = cw.Alarm(
            self,
            "AdmissionDenialsSpikeAlarm",
            alarm_name=f"cloudguard-admission-denials-spike-{environment_name}",
            alarm_description="Surge in rate limit or worker admission denials.",
            metric=admission_denied_metric,
            threshold=50,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self.alarms.append(self.admission_denials_alarm)

        # ---------------------------------------------------------------------
        # 2. CloudWatch Operational Dashboard
        # ---------------------------------------------------------------------
        dashboard_name = f"CloudGuard-Operations-{environment_name}"
        self.dashboard = cw.Dashboard(
            self,
            "CloudGuardDashboard",
            dashboard_name=dashboard_name,
        )

        # Row 0: Header / Privacy note
        self.dashboard.add_widgets(
            cw.TextWidget(
                markdown=(
                    f"## CloudGuard AI — Operational & Security Telemetry ({environment_name})\n"
                    "Real-time operational signals, request latencies, admission budgets, "
                    "agent workflow health, and estimated AI inference cost.\n\n"
                    "> **Privacy Boundary:** Prompts, evidence bodies, credentials, tokens, "
                    "and user/document identifiers are strictly excluded from metric dimensions."
                ),
                width=24,
                height=2,
            )
        )

        # Row 1: Ingestion Pipeline Health
        ingestion_started_metric = ingestion_state_machine.metric_started(
            period=cdk.Duration.minutes(5), statistic="Sum"
        )
        ingestion_succeeded_metric = ingestion_state_machine.metric_succeeded(
            period=cdk.Duration.minutes(5), statistic="Sum"
        )
        processor_duration_metric = processing_function.metric_duration(
            period=cdk.Duration.minutes(5), statistic="Average"
        )
        processor_invocations_metric = processing_function.metric_invocations(
            period=cdk.Duration.minutes(5), statistic="Sum"
        )

        self.dashboard.add_widgets(
            cw.GraphWidget(
                title="Document Ingestion State Machine Executions",
                width=12,
                height=6,
                left=[ingestion_started_metric, ingestion_succeeded_metric, ingestion_failed_metric],
            ),
            cw.GraphWidget(
                title="Ingestion DLQ Messages & Processor Errors",
                width=12,
                height=6,
                left=[dlq_messages_metric, processor_error_metric, processor_invocations_metric],
                right=[processor_duration_metric],
            ),
        )

        # Row 2: Agent Workflows & Decision Health
        workflow_started_metric = agent_workflow_state_machine.metric_started(
            period=cdk.Duration.minutes(5), statistic="Sum"
        )
        workflow_succeeded_metric = agent_workflow_state_machine.metric_succeeded(
            period=cdk.Duration.minutes(5), statistic="Sum"
        )
        workflow_duration_metric = agent_workflow_state_machine.metric_time(
            period=cdk.Duration.minutes(5), statistic="Average"
        )

        agent_left_metrics: list[cw.IMetric] = [
            workflow_started_metric,
            workflow_succeeded_metric,
            workflow_failed_metric,
        ]

        agent_lambda_errors: list[cw.IMetric] = []
        for name, fn in [
            ("Research", research_function),
            ("Risk", risk_function),
            ("Reviewer", reviewer_function),
        ]:
            if fn is not None:
                agent_lambda_errors.append(
                    fn.metric_errors(period=cdk.Duration.minutes(5), statistic="Sum")
                )

        self.dashboard.add_widgets(
            cw.GraphWidget(
                title="Agent Workflow Executions & Step Health",
                width=12,
                height=6,
                left=agent_left_metrics,
                right=[workflow_duration_metric],
            ),
            cw.GraphWidget(
                title="Agent Lambda Execution Errors",
                width=12,
                height=6,
                left=agent_lambda_errors if agent_lambda_errors else [workflow_throttled_metric],
            ),
        )

        # Row 3: API Admission, Rate Limits & DynamoDB
        admission_allowed_metric = cw.Metric(
            namespace=CLOUDWATCH_NAMESPACE,
            metric_name="AdmissionAllowed",
            dimensions_map={"Environment": environment_name},
            period=cdk.Duration.minutes(5),
            statistic="Sum",
        )
        read_capacity_metric = rate_limit_table.metric_consumed_read_capacity_units(
            period=cdk.Duration.minutes(5), statistic="Sum"
        )
        write_capacity_metric = rate_limit_table.metric_consumed_write_capacity_units(
            period=cdk.Duration.minutes(5), statistic="Sum"
        )

        self.dashboard.add_widgets(
            cw.GraphWidget(
                title="API Request & Worker Admission Decisions",
                width=12,
                height=6,
                left=[admission_allowed_metric],
                right=[admission_denied_metric],
            ),
            cw.GraphWidget(
                title="Rate Limit DynamoDB Consumed Capacity Units",
                width=12,
                height=6,
                left=[read_capacity_metric, write_capacity_metric],
                right=[dynamo_errors_metric],
            ),
        )

        # Row 4: Custom Operational Latencies & Token/Cost Telemetry
        rag_latency_p50 = cw.Metric(
            namespace=CLOUDWATCH_NAMESPACE,
            metric_name="OperationDurationMs",
            dimensions_map={"Environment": environment_name, "Operation": "rag_generate_answer"},
            period=cdk.Duration.minutes(5),
            statistic="p50",
        )
        rag_latency_p95 = cw.Metric(
            namespace=CLOUDWATCH_NAMESPACE,
            metric_name="OperationDurationMs",
            dimensions_map={"Environment": environment_name, "Operation": "rag_generate_answer"},
            period=cdk.Duration.minutes(5),
            statistic="p95",
        )
        retrieval_latency_p95 = cw.Metric(
            namespace=CLOUDWATCH_NAMESPACE,
            metric_name="OperationDurationMs",
            dimensions_map={"Environment": environment_name, "Operation": "retrieval_search"},
            period=cdk.Duration.minutes(5),
            statistic="p95",
        )

        token_input_metric = cw.Metric(
            namespace=CLOUDWATCH_NAMESPACE,
            metric_name="InputTokens",
            dimensions_map={"Environment": environment_name},
            period=cdk.Duration.minutes(5),
            statistic="Sum",
        )
        token_output_metric = cw.Metric(
            namespace=CLOUDWATCH_NAMESPACE,
            metric_name="OutputTokens",
            dimensions_map={"Environment": environment_name},
            period=cdk.Duration.minutes(5),
            statistic="Sum",
        )
        cost_metric = cw.Metric(
            namespace=CLOUDWATCH_NAMESPACE,
            metric_name="EstimatedInferenceCostUSD",
            dimensions_map={"Environment": environment_name},
            period=cdk.Duration.minutes(5),
            statistic="Sum",
        )
        cost_measured_queries_metric = cw.Metric(
            namespace=CLOUDWATCH_NAMESPACE,
            metric_name="CostMeasuredQueries",
            dimensions_map={"Environment": environment_name},
            period=cdk.Duration.minutes(5),
            statistic="Sum",
        )
        cost_unmeasured_queries_metric = cw.Metric(
            namespace=CLOUDWATCH_NAMESPACE,
            metric_name="CostUnmeasuredQueries",
            dimensions_map={"Environment": environment_name},
            period=cdk.Duration.minutes(5),
            statistic="Sum",
        )
        # Cost-per-query metric math: IF(measured > 0, cost / measured)
        # Supported CloudWatch conditional omitting zero/missing-denominator periods
        # rather than fabricating a zero cost. Only queries where model pricing is known
        # are in CostMeasuredQueries; unmeasured/fixture runs are in CostUnmeasuredQueries.
        cost_per_measured_query = cw.MathExpression(
            expression="IF(measured > 0, cost / measured)",
            using_metrics={
                "cost": cost_metric,
                "measured": cost_measured_queries_metric,
            },
            period=cdk.Duration.minutes(5),
            label="EstimatedCostPerMeasuredQueryUSD",
        )

        self.dashboard.add_widgets(
            cw.GraphWidget(
                title="RAG & Retrieval Latency Percentiles (ms)",
                width=8,
                height=6,
                left=[rag_latency_p50, rag_latency_p95, retrieval_latency_p95],
            ),
            cw.GraphWidget(
                title="Model Token Usage & Total Cost",
                width=8,
                height=6,
                left=[token_input_metric, token_output_metric],
                right=[cost_metric],
            ),
            cw.GraphWidget(
                title="Estimated Cost Per Query & Pricing Coverage",
                width=8,
                height=6,
                left=[cost_per_measured_query],
                right=[cost_measured_queries_metric, cost_unmeasured_queries_metric],
            ),
        )

        # Row 5: Operational Telemetry & Cost Coverage Documentation
        self.dashboard.add_widgets(
            cw.TextWidget(
                markdown=(
                    "### Telemetry Notes & Cost Coverage\n"
                    "- **Estimated Cost Per Query Denominator:** Computed as "
                    "`IF(measured > 0, cost / measured)` where `cost` is `EstimatedInferenceCostUSD` "
                    "and `measured` is `CostMeasuredQueries`. "
                    "Only queries executed against recognized models with catalog pricing "
                    "(us-east-1 On-Demand) are counted in `CostMeasuredQueries`. "
                    "Zero/missing-denominator periods are omitted rather than fabricating zero cost.\n"
                    "- **Partial Coverage:** Fixture/mock/unlisted model calls emit "
                    "`CostUnmeasuredQueries` with unmeasured cost (`None`) and are excluded from "
                    "the denominator to avoid artificially deflating cost-per-query.\n"
                    "- **CloudWatch Cost & Latency:** Metrics are emitted via EMF to stdout and "
                    "ingested by CloudWatch Agent asynchronously. CloudWatch custom metrics and "
                    "log ingestion incur AWS charges. Missing data is treated as "
                    "`NOT_BREACHING` to avoid false alarms during idle periods."
                ),
                width=24,
                height=3,
            )
        )

