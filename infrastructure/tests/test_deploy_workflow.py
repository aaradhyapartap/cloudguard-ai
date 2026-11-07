"""Tests for .github/workflows/deploy.yml structure and environment variable scoping."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

DEPLOY_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"
)


@pytest.fixture
def deploy_workflow() -> dict[str, Any]:
    with open(DEPLOY_WORKFLOW_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    return data


def test_preflight_safety_job_has_no_environment_scope_or_env_vars(
    deploy_workflow: dict[str, Any],
) -> None:
    """Preflight job must run at repo scope without environment scope or env vars."""
    jobs = deploy_workflow.get("jobs", {})
    assert "preflight-safety" in jobs
    preflight = jobs["preflight-safety"]

    # Preflight must NOT specify an environment: key
    assert "environment" not in preflight

    # Preflight steps must NOT attempt to reference environment-scoped vars
    forbidden_vars = {
        "vars.AWS_AURORA_CLUSTER_ARN",
        "vars.AWS_AURORA_SECRET_ARN",
        "vars.BEDROCK_GUARDRAIL_ID",
        "vars.BEDROCK_GUARDRAIL_VERSION",
        "vars.AWS_DEPLOY_ROLE_ARN",
    }
    steps = preflight.get("steps", [])
    for step in steps:
        step_str = yaml.dump(step)
        for forbidden in forbidden_vars:
            assert forbidden not in step_str, (
                f"Step '{step.get('name')}' in environment-less preflight-safety references '{forbidden}'"
            )


def test_cdk_action_job_has_environment_scope_and_pre_credential_validation(
    deploy_workflow: dict[str, Any],
) -> None:
    """cdk-action must define environment scope and validate env vars before AWS credentials."""
    jobs = deploy_workflow.get("jobs", {})
    assert "cdk-action" in jobs
    cdk_action = jobs["cdk-action"]

    # Must specify target environment
    assert cdk_action.get("environment") == "${{ github.event.inputs.environment }}"

    steps = cdk_action.get("steps", [])
    step_names = [s.get("name") for s in steps]

    assert "Validate Environment-Specific Variables" in step_names
    assert "Configure AWS Credentials via OIDC" in step_names

    val_idx = step_names.index("Validate Environment-Specific Variables")
    cred_idx = step_names.index("Configure AWS Credentials via OIDC")

    # Validation MUST occur strictly BEFORE credential assumption
    assert val_idx < cred_idx, (
        f"Validation step (idx {val_idx}) must precede credential acquisition (idx {cred_idx})"
    )


def _simulate_env_validation(
    *,
    target_env: str,
    target_action: str,
    deploy_role_arn: str,
    aurora_cluster_arn: str = "",
    aurora_secret_arn: str = "",
    guardrail_id: str = "",
    guardrail_version: str = "",
) -> None:
    """Simulate the bash validation logic inside Validate Environment-Specific Variables."""
    deploy_role_pattern = re.compile(
        rf"^arn:aws:iam:[a-z0-9-]*:[0-9]{{12}}:role/cloudguard-github-deploy-{target_env}$"
    )
    if not deploy_role_pattern.match(deploy_role_arn):
        raise ValueError(f"Invalid AWS_DEPLOY_ROLE_ARN: '{deploy_role_arn}'")

    if target_action == "deploy":
        cluster_pattern = re.compile(
            r"^arn:aws:rds:[a-z0-9-]+:[0-9]{12}:cluster:[a-zA-Z0-9_-]+$"
        )
        secret_pattern = re.compile(
            r"^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[a-zA-Z0-9_/-]+$"
        )
        if not cluster_pattern.match(aurora_cluster_arn):
            raise ValueError(f"Invalid AWS_AURORA_CLUSTER_ARN: '{aurora_cluster_arn}'")
        if not secret_pattern.match(aurora_secret_arn):
            raise ValueError(f"Invalid AWS_AURORA_SECRET_ARN: '{aurora_secret_arn}'")

        if target_env == "prod" and (not guardrail_id or not guardrail_version):
            raise ValueError("Missing BEDROCK_GUARDRAIL_ID or BEDROCK_GUARDRAIL_VERSION")


def test_environment_scope_validation_matrix() -> None:
    """Test validation logic with variables supplied strictly at environment scope."""
    valid_role_dev = "arn:aws:iam::123456789012:role/cloudguard-github-deploy-dev"
    valid_role_prod = "arn:aws:iam::123456789012:role/cloudguard-github-deploy-prod"
    valid_cluster = "arn:aws:rds:us-east-1:123456789012:cluster:cloudguard-cluster"
    valid_secret = "arn:aws:secretsmanager:us-east-1:123456789012:secret:db-creds-abc"

    # 1. Dev diff only requires deploy role
    _simulate_env_validation(
        target_env="dev",
        target_action="diff",
        deploy_role_arn=valid_role_dev,
    )

    # 2. Dev deploy requires deploy role + Aurora ARNs
    _simulate_env_validation(
        target_env="dev",
        target_action="deploy",
        deploy_role_arn=valid_role_dev,
        aurora_cluster_arn=valid_cluster,
        aurora_secret_arn=valid_secret,
    )

    # 3. Dev deploy with missing cluster ARN must fail
    with pytest.raises(ValueError, match="Invalid AWS_AURORA_CLUSTER_ARN"):
        _simulate_env_validation(
            target_env="dev",
            target_action="deploy",
            deploy_role_arn=valid_role_dev,
            aurora_cluster_arn="",
            aurora_secret_arn=valid_secret,
        )

    # 4. Prod deploy without guardrail must fail
    with pytest.raises(ValueError, match="Missing BEDROCK_GUARDRAIL_ID"):
        _simulate_env_validation(
            target_env="prod",
            target_action="deploy",
            deploy_role_arn=valid_role_prod,
            aurora_cluster_arn=valid_cluster,
            aurora_secret_arn=valid_secret,
            guardrail_id="",
            guardrail_version="",
        )

    # 5. Prod deploy with complete guardrail succeeds
    _simulate_env_validation(
        target_env="prod",
        target_action="deploy",
        deploy_role_arn=valid_role_prod,
        aurora_cluster_arn=valid_cluster,
        aurora_secret_arn=valid_secret,
        guardrail_id="gr-12345",
        guardrail_version="1",
    )
