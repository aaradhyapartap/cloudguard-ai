"""Shared fixtures for infrastructure tests."""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk import assertions

from stacks.agent_workflow_stack import AgentWorkflowStack
from stacks.ingestion_stack import IngestionStack


@pytest.fixture(scope="module")
def ingestion_template() -> assertions.Template:
    """Synthesize the IngestionStack and return its CloudFormation template."""
    app = cdk.App()
    stack = IngestionStack(
        app,
        "TestIngestion",
        environment_name="dev",
        aurora_cluster_arn="arn:aws:rds:us-east-1:123456789012:cluster:test-cluster",
        aurora_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",
        event_bus_name="cloudguard-events",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return assertions.Template.from_stack(stack)

@pytest.fixture(scope="module")
def agent_workflow_template() -> assertions.Template:
    """Synthesize the Phase 6 deterministic agent workflow stack."""
    app = cdk.App()
    stack = AgentWorkflowStack(
        app,
        "TestAgentWorkflow",
        environment_name="dev",
        aurora_cluster_arn="arn:aws:rds:us-east-1:123456789012:cluster:test-cluster",
        aurora_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret",
        chat_model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        reviewer_model_id="amazon.nova-pro-v1:0",
        embedding_model_id="amazon.titan-embed-text-v2:0",
        agentic_workflows_enabled=True,
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return assertions.Template.from_stack(stack)
