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
    cdk synth                  # renders templates, touches no AWS resources
    cdk deploy CloudGuardIdentity-dev
"""

from __future__ import annotations

import os

import aws_cdk as cdk

from stacks.identity_stack import IdentityStack

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

# Tag everything. Untagged resources are how a student account quietly grows a
# bill nobody can attribute.
cdk.Tags.of(app).add("Project", "cloudguard-ai")
cdk.Tags.of(app).add("Environment", environment_name)
cdk.Tags.of(app).add("ManagedBy", "cdk")

app.synth()
