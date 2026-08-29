#!/usr/bin/env python3
"""CDK application.

Infrastructure arrives with the phase that needs it, not all at once. Phase 2
brings identity because §27 of the brief forbids creating production
infrastructure through the console, and Phase 2 needs a user pool. Later phases
append stacks to this same app.

Stacks are separated by lifecycle rather than by service. A user pool outlives
several redeploys of the API, and deleting it deletes everyone's account — so it
does not belong in the same stack as a Lambda that changes weekly.

    cd infrastructure
    pip install -r requirements.txt
    cdk synth -c offline_synth=true   # offline template rendering with placeholders
    cdk deploy CloudGuardIdentity-dev
"""

from __future__ import annotations

import os

import aws_cdk as cdk

from stacks.identity_stack import IdentityStack
from stacks.ingestion_stack import IngestionStack

app = cdk.App()

environment_name = app.node.try_get_context("env") or "dev"
env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION", "us-east-1"),
)

identity = IdentityStack(
    app,
    f"CloudGuardIdentity-{environment_name}",
    environment_name=environment_name,
    env=env,
    description="Cognito user pool, groups and app client for CloudGuard AI",
)

# -----------------------------------------------------------------------------
# Aurora ARN Configuration & Offline Synthesis Safety Model
# -----------------------------------------------------------------------------
# CloudGuard uses Aurora Serverless v2 via the RDS Data API (ADR-0008).
# The Data API repository requires the Aurora cluster ARN and Secrets Manager ARN.
#
# SAFETY MODEL (Secure by Default):
# 1. DEPLOYMENT & DEFAULT INVOCATION (Secure Default):
#    - Real, valid Aurora cluster and secret ARNs are strictly required for ALL
#      environments (dev, staging, prod).
#    - Plain `cdk deploy` and `cdk synth` without real ARNs will FAIL fast.
#    - Example deployment:
#        cdk deploy -c aurora_cluster_arn=arn:aws:rds:us-east-1:123456789012:cluster:cloudguard \
#                   -c aurora_secret_arn=arn:aws:secretsmanager:us-east-1:123456789012:secret:cloudguard
#
# 2. EXPLICIT OFFLINE SYNTHESIS / TESTING MODE:
#    - For credential-free local template rendering and unit testing, placeholder
#      ARNs are allowed ONLY when offline mode is explicitly requested:
#        - Context flag: -c offline_synth=true
#        - Environment variable: CDK_OFFLINE_SYNTH=1
#    - WARNING: Offline mode is strictly for local synthesis/tests and must NEVER
#      be used for real AWS deployments.
#    - Production and staging environments ('prod', 'staging') strictly forbid
#      placeholder ARNs under all circumstances.
# -----------------------------------------------------------------------------

raw_cluster_arn = (
    app.node.try_get_context("aurora_cluster_arn")
    or os.getenv("AWS_AURORA_CLUSTER_ARN")
)
raw_secret_arn = (
    app.node.try_get_context("aurora_secret_arn")
    or os.getenv("AWS_AURORA_SECRET_ARN")
)

is_offline_synth = (
    app.node.try_get_context("offline_synth") in ("true", "1", True, "yes")
    or app.node.try_get_context("offline") in ("true", "1", True, "yes")
    or os.getenv("CDK_OFFLINE_SYNTH") == "1"
    or os.getenv("OFFLINE_SYNTH") == "1"
)

# Offline mode with placeholder ARNs is strictly restricted to 'dev' / local testing.
# Production/staging environments can NEVER bypass real ARN validation.
is_production_like = environment_name in ("prod", "staging") or environment_name != "dev"

allow_placeholders = is_offline_synth and not is_production_like

if not allow_placeholders:
    if not raw_cluster_arn or "placeholder" in raw_cluster_arn:
        raise ValueError(
            f"CloudGuard synthesis/deployment for environment '{environment_name}' requires a real 'aurora_cluster_arn' "
            "context parameter (e.g. cdk deploy -c aurora_cluster_arn=arn:aws:rds:...).\n"
            "For credential-free local synthesis/testing, explicitly pass -c offline_synth=true or set CDK_OFFLINE_SYNTH=1."
        )
    if not raw_secret_arn or "placeholder" in raw_secret_arn:
        raise ValueError(
            f"CloudGuard synthesis/deployment for environment '{environment_name}' requires a real 'aurora_secret_arn' "
            "context parameter (e.g. cdk deploy -c aurora_secret_arn=arn:aws:secretsmanager:...).\n"
            "For credential-free local synthesis/testing, explicitly pass -c offline_synth=true or set CDK_OFFLINE_SYNTH=1."
        )

aurora_cluster_arn = (
    raw_cluster_arn
    or "arn:aws:rds:us-east-1:123456789012:cluster:cloudguard-placeholder"
)
aurora_secret_arn = (
    raw_secret_arn
    or "arn:aws:secretsmanager:us-east-1:123456789012:secret:cloudguard-placeholder"
)
event_bus_name = app.node.try_get_context("event_bus_name") or "cloudguard-events"

ingestion = IngestionStack(
    app,
    f"CloudGuardIngestion-{environment_name}",
    environment_name=environment_name,
    aurora_cluster_arn=aurora_cluster_arn,
    aurora_secret_arn=aurora_secret_arn,
    event_bus_name=event_bus_name,
    env=env,
    description="S3 document bucket, Step Functions ingestion workflow, and processing Lambda",
)

# Tag everything. Untagged resources are how a student account quietly grows a
# bill nobody can attribute.
cdk.Tags.of(app).add("Project", "cloudguard-ai")
cdk.Tags.of(app).add("Environment", environment_name)
cdk.Tags.of(app).add("ManagedBy", "cdk")

app.synth()
