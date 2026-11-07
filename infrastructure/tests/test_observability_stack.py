"""CDK assertions and configuration tests for ObservabilityStack."""

from __future__ import annotations

import json

import aws_cdk as cdk
import pytest
from aws_cdk import assertions

from stacks.agent_workflow_stack import AgentWorkflowStack
from stacks.ingestion_stack import IngestionStack
from stacks.observability_stack import CLOUDWATCH_NAMESPACE, ObservabilityStack
from stacks.rate_limit_stack import RateLimitStack


@pytest.fixture(scope="module")
def observability_template() -> tuple[assertions.Template, ObservabilityStack]:
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")

    rate_limits = RateLimitStack(
        app,
        "TestRateLimits",
        environment_name="dev",
        env=env,
    )
    ingestion = IngestionStack(
        app,
        "TestIngestion",
        environment_name="dev",
        aurora_cluster_arn="arn:aws:rds:us-east-1:123456789012:cluster:test-cluster",
        aurora_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",
        event_bus_name="cloudguard-events",
        admission_table_name=rate_limits.table.table_name,
        admission_table_arn=rate_limits.table.table_arn,
        env=env,
    )
    agent_workflow = AgentWorkflowStack(
        app,
        "TestAgentWorkflow",
        environment_name="dev",
        aurora_cluster_arn="arn:aws:rds:us-east-1:123456789012:cluster:test-cluster",
        aurora_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        reviewer_model_id="amazon.nova-pro-v1:0",
        embedding_model_id="amazon.titan-embed-text-v2:0",
        agentic_workflows_enabled=True,
        admission_table_name=rate_limits.table.table_name,
        admission_table_arn=rate_limits.table.table_arn,
        env=env,
    )

    obs_stack = ObservabilityStack(
        app,
        "TestObservability",
        environment_name="dev",
        ingestion_dlq=ingestion.ingestion_dlq,
        ingestion_state_machine=ingestion.state_machine,
        processing_function=ingestion.processing_function,
        agent_workflow_state_machine=agent_workflow.state_machine,
        rate_limit_table=rate_limits.table,
        documents_bucket=ingestion.documents_bucket,
        research_function=agent_workflow.research_function,
        risk_function=agent_workflow.risk_function,
        reviewer_function=agent_workflow.reviewer_function,
        env=env,
    )
    return assertions.Template.from_stack(obs_stack), obs_stack


def test_observability_creates_dashboard(observability_template: tuple[assertions.Template, ObservabilityStack]) -> None:
    template, _ = observability_template
    dashboards = template.find_resources("AWS::CloudWatch::Dashboard")
    assert len(dashboards) == 1
    dashboard_props = next(iter(dashboards.values()))["Properties"]
    assert dashboard_props["DashboardName"] == "CloudGuard-Operations-dev"

    raw_body = dashboard_props["DashboardBody"]
    if isinstance(raw_body, str):
        body_text = raw_body
    elif isinstance(raw_body, dict) and "Fn::Join" in raw_body:
        # Reconstruct string from Fn::Join parts
        parts = raw_body["Fn::Join"][1]
        body_text = "".join(p if isinstance(p, str) else json.dumps(p) for p in parts)
    else:
        body_text = json.dumps(raw_body)

    # Verify header widget mentions privacy boundaries and contains key widget titles
    assert "Privacy Boundary" in body_text
    assert "Document Ingestion State Machine Executions" in body_text
    assert "Agent Workflow Executions & Step Health" in body_text
    assert "API Request & Worker Admission Decisions" in body_text
    assert "RAG & Retrieval Latency Percentiles" in body_text
    assert "Model Token Usage & Total Cost" in body_text
    assert "Estimated Cost Per Query & Pricing Coverage" in body_text
    assert "CostMeasuredQueries" in body_text
    assert "IF(measured > 0, cost / measured)" in body_text


def test_observability_creates_required_alarms(observability_template: tuple[assertions.Template, ObservabilityStack]) -> None:
    template, _ = observability_template
    alarms = template.find_resources("AWS::CloudWatch::Alarm")
    # At least 7 operational failure & budget alarms
    assert len(alarms) >= 7

    # Ingestion DLQ alarm
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "MetricName": "ApproximateNumberOfMessagesVisible",
            "Threshold": 1,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
    )

    # Ingestion Step Functions failure alarm
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "MetricName": "ExecutionsFailed",
            "Threshold": 1,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
    )

    # Document Processor Lambda error alarm
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "MetricName": "Errors",
            "Threshold": 1,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
    )

    # Custom admission denial spike alarm
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "Namespace": CLOUDWATCH_NAMESPACE,
            "MetricName": "AdmissionDenied",
            "Threshold": 50,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
    )


def test_alarm_dimensions_are_strictly_bounded(observability_template: tuple[assertions.Template, ObservabilityStack]) -> None:
    template, _ = observability_template
    alarms = template.find_resources("AWS::CloudWatch::Alarm")

    prohibited_substrings = ("user", "tenant", "org", "document", "prompt", "token")
    for alarm_id, alarm in alarms.items():
        dimensions = alarm["Properties"].get("Dimensions", [])
        for dim in dimensions:
            dim_name = dim["Name"].lower()
            assert not any(p in dim_name for p in prohibited_substrings), (
                f"Alarm {alarm_id} uses sensitive dimension {dim['Name']}"
            )


def test_dynamodb_system_errors_alarm_monitors_actual_operations(
    observability_template: tuple[assertions.Template, ObservabilityStack],
) -> None:
    """Verify DynamoDB SystemErrors alarm monitors UpdateItem, PutItem, DeleteItem operations."""
    template, _ = observability_template
    alarms = template.find_resources("AWS::CloudWatch::Alarm")

    # Find the rate limit table system errors alarm
    dynamo_alarm = None
    for alarm in alarms.values():
        name = alarm["Properties"].get("AlarmName", "")
        if "rate-limit-table-errors" in name:
            dynamo_alarm = alarm
            break

    assert dynamo_alarm is not None, "RateLimitTableSystemErrorsAlarm not found"
    props = dynamo_alarm["Properties"]

    # Must use metric math across the operations used by DynamoDBRateLimitStore & DynamoDBWorkLeaseStore
    metrics = props.get("Metrics", [])
    assert len(metrics) >= 4, "Expected math expression and at least 3 operation metrics"

    # Find math expression
    math_expr = next((m for m in metrics if "Expression" in m), None)
    assert math_expr is not None
    assert "m0 + m1 + m2" in math_expr["Expression"]

    # Verify operations monitored: UpdateItem, PutItem, DeleteItem
    monitored_operations = set()
    for m in metrics:
        metric_stat = m.get("MetricStat", {})
        metric_def = metric_stat.get("Metric", {})
        if metric_def.get("Namespace") == "AWS/DynamoDB" and metric_def.get("MetricName") == "SystemErrors":
            dims = {d["Name"]: d["Value"] for d in metric_def.get("Dimensions", [])}
            assert "TableName" in dims
            if "Operation" in dims:
                monitored_operations.add(dims["Operation"])

    assert monitored_operations == {"UpdateItem", "PutItem", "DeleteItem"}


def test_emitted_metric_identities_match_dashboard_and_alarm_queries(
    observability_template: tuple[assertions.Template, ObservabilityStack],
) -> None:
    """Verify that synthesized dashboard and alarms query metrics that match the EMF emission contract."""
    template, _ = observability_template
    dashboards = template.find_resources("AWS::CloudWatch::Dashboard")
    assert len(dashboards) == 1
    dashboard_props = next(iter(dashboards.values()))["Properties"]

    raw_body = dashboard_props["DashboardBody"]
    if isinstance(raw_body, str):
        body_text = raw_body
    elif isinstance(raw_body, dict) and "Fn::Join" in raw_body:
        parts = raw_body["Fn::Join"][1]
        body_text = "".join(p if isinstance(p, str) else json.dumps(p) for p in parts)
    else:
        body_text = json.dumps(raw_body)

    # Dashboard must reference valid custom metrics in CloudGuardAI namespace
    expected_cloudguard_metrics = [
        "AdmissionAllowed",
        "AdmissionDenied",
        "OperationDurationMs",
        "InputTokens",
        "OutputTokens",
        "EstimatedInferenceCostUSD",
        "CostMeasuredQueries",
        "CostUnmeasuredQueries",
    ]
    for metric_name in expected_cloudguard_metrics:
        assert metric_name in body_text, f"Metric {metric_name} missing from dashboard body"

    # Dashboard must reference specific operations matching service instrumentation
    assert "rag_generate_answer" in body_text
    assert "retrieval_search" in body_text

    # Dashboard must NOT contain sensitive dimensions
    prohibited = ["user_id", "organization_id", "document_id", "tenant_id", "auth_token"]
    for p in prohibited:
        assert p not in body_text.lower(), f"Prohibited dimension key {p} in dashboard"
