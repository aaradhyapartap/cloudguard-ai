"""Template assertions for the Phase 3 ingestion infrastructure stack.

These tests synthesize the IngestionStack and verify the CloudFormation template
contains the expected resources with correct configurations, without deploying
anything to AWS.
"""

from __future__ import annotations

import json

from aws_cdk import assertions


def test_s3_bucket_exists_with_eventbridge(
    ingestion_template: assertions.Template,
) -> None:
    """The documents bucket must exist with EventBridge notifications enabled."""
    ingestion_template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketName": assertions.Match.string_like_regexp(
                r"cloudguard-documents-.*"
            ),
            "VersioningConfiguration": {"Status": "Enabled"},
        },
    )
    # EventBridge notification configuration
    ingestion_template.has_resource_properties(
        "Custom::S3BucketNotifications",
        assertions.Match.object_like({}),
    )


def test_lambda_function_exists_outside_vpc(
    ingestion_template: assertions.Template,
) -> None:
    """The processing Lambda must exist as a container image and must NOT be attached to a VPC."""
    ingestion_template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": assertions.Match.string_like_regexp(
                r"cloudguard-document-processor-.*"
            ),
            "PackageType": "Image",
            "ImageConfig": {
                "Command": ["app.deployed_document_worker.handler"],
            },
            "Timeout": 300,
        },
    )

    # Verify NO Lambda has VpcConfig — the processing function must stay
    # outside the VPC per ADR-0008.
    template_json = ingestion_template.to_json()
    for logical_id, resource in template_json.get("Resources", {}).items():
        if resource.get("Type") == "AWS::Lambda::Function":
            properties = resource.get("Properties", {})
            assert "VpcConfig" not in properties, (
                f"Lambda {logical_id} has VpcConfig — deployed Lambda must "
                f"remain outside the VPC (ADR-0008)"
            )


def test_step_functions_state_machine_exists(
    ingestion_template: assertions.Template,
) -> None:
    """A Standard Step Functions state machine must exist."""
    ingestion_template.has_resource_properties(
        "AWS::StepFunctions::StateMachine",
        {
            "StateMachineName": assertions.Match.string_like_regexp(
                r"cloudguard-ingestion-.*"
            ),
            "StateMachineType": "STANDARD",
        },
    )


def test_state_machine_normalizes_s3_key(
    ingestion_template: assertions.Template,
) -> None:
    """The state machine definition must extract organization_id and document_id
    from the S3 key using Step Functions intrinsic functions."""
    template_json = ingestion_template.to_json()

    for _logical_id, resource in template_json.get("Resources", {}).items():
        if resource.get("Type") != "AWS::StepFunctions::StateMachine":
            continue

        definition_string = resource["Properties"].get("DefinitionString", "")
        # DefinitionString may be a Fn::Join or an intrinsic — resolve it
        if isinstance(definition_string, str):
            definition = json.loads(definition_string)
        else:
            # It's a CloudFormation intrinsic (Fn::Join, etc.) —
            # flatten to string for substring search.
            definition = json.dumps(definition_string)

        definition_text = (
            json.dumps(definition) if isinstance(definition, dict) else definition
        )

        assert "States.StringSplit" in definition_text, (
            "State machine must use States.StringSplit to parse the S3 key"
        )
        assert "States.ArrayGetItem" in definition_text, (
            "State machine must use States.ArrayGetItem to extract UUIDs"
        )
        return

    raise AssertionError("No Step Functions state machine found in template")


def test_sqs_dlq_exists(
    ingestion_template: assertions.Template,
) -> None:
    """An SQS dead-letter queue must exist for failed ingestions."""
    ingestion_template.has_resource_properties(
        "AWS::SQS::Queue",
        {
            "QueueName": assertions.Match.string_like_regexp(
                r"cloudguard-ingestion-dlq-.*"
            ),
        },
    )


def test_eventbridge_rule_exists(
    ingestion_template: assertions.Template,
) -> None:
    """An EventBridge rule must exist matching S3 Object Created events."""
    ingestion_template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "EventPattern": assertions.Match.object_like(
                {
                    "source": ["aws.s3"],
                    "detail-type": ["Object Created"],
                }
            ),
        },
    )


def test_eventbridge_delivery_dlq_configured(
    ingestion_template: assertions.Template,
) -> None:
    """The EventBridge rule target must configure an SQS dead-letter queue for delivery failures."""
    ingestion_template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Targets": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {
                            "Arn": assertions.Match.any_value(),
                            "DeadLetterConfig": assertions.Match.object_like(
                                {
                                    "Arn": assertions.Match.any_value(),
                                }
                            ),
                        }
                    ),
                ]
            ),
        },
    )


def test_step_functions_execution_failure_dlq_configured(
    ingestion_template: assertions.Template,
) -> None:
    """The Step Functions state machine definition must catch execution failures
    and route failure records to the SQS DLQ."""
    template_json = ingestion_template.to_json()

    for _logical_id, resource in template_json.get("Resources", {}).items():
        if resource.get("Type") != "AWS::StepFunctions::StateMachine":
            continue

        definition_string = resource["Properties"].get("DefinitionString", "")
        if isinstance(definition_string, str):
            definition_text = definition_string
        else:
            definition_text = json.dumps(definition_string)

        assert ":states:::sqs:sendMessage" in definition_text, (
            "State machine must include an SQS SendMessage task for execution failures"
        )
        assert "SendFailureToDLQ" in definition_text, (
            "State machine must include a SendFailureToDLQ state"
        )
        assert "ErrorDetails" in definition_text or "Catch" in definition_text, (
            "State machine must catch task failures"
        )
        return

    raise AssertionError("No Step Functions state machine found in template")


def test_no_nat_gateway(
    ingestion_template: assertions.Template,
) -> None:
    """The stack must not contain a NAT Gateway — Lambda stays outside the VPC."""
    template_json = ingestion_template.to_json()
    for _logical_id, resource in template_json.get("Resources", {}).items():
        assert resource.get("Type") != "AWS::EC2::NatGateway", (
            "NAT Gateway found — the architecture forbids NAT Gateways"
        )


def test_lambda_has_data_api_permissions(
    ingestion_template: assertions.Template,
) -> None:
    """The processing Lambda role must have Data API permissions."""
    ingestion_template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": assertions.Match.object_like(
                {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Action": assertions.Match.array_with(
                                        ["rds-data:ExecuteStatement"]
                                    ),
                                    "Effect": "Allow",
                                }
                            ),
                        ]
                    ),
                }
            ),
        },
    )


def test_lambda_has_s3_read_permission(
    ingestion_template: assertions.Template,
) -> None:
    """The processing Lambda role must have S3 GetObject permission."""
    ingestion_template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": assertions.Match.object_like(
                {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Action": assertions.Match.array_with(
                                        ["s3:GetObject*"]
                                    ),
                                    "Effect": "Allow",
                                }
                            ),
                        ]
                    ),
                }
            ),
        },
    )


def test_custom_event_bus_exists(
    ingestion_template: assertions.Template,
) -> None:
    """A custom EventBridge event bus must exist with the configured name."""
    ingestion_template.has_resource_properties(
        "AWS::Events::EventBus",
        {
            "Name": "cloudguard-events",
        },
    )


def test_worker_has_event_bus_name_environment_variable(
    ingestion_template: assertions.Template,
) -> None:
    """The processing Lambda environment must reference the custom EventBridge bus name."""
    ingestion_template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "EVENT_PUBLISHER": "eventbridge",
                        "AWS_EVENT_BUS_NAME": assertions.Match.object_like(
                            {"Ref": assertions.Match.string_like_regexp(r"EventBus.*")}
                        ),
                    }
                ),
            },
        },
    )


def test_lambda_has_eventbridge_put_events_permission_on_custom_bus(
    ingestion_template: assertions.Template,
) -> None:
    """The processing Lambda role must have events:PutEvents permission scoped to the custom EventBus."""
    ingestion_template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": assertions.Match.object_like(
                {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Action": "events:PutEvents",
                                    "Effect": "Allow",
                                    "Resource": assertions.Match.object_like(
                                        {
                                            "Fn::GetAtt": assertions.Match.array_with(
                                                [
                                                    assertions.Match.string_like_regexp(
                                                        r"EventBus.*"
                                                    ),
                                                    "Arn",
                                                ]
                                            )
                                        }
                                    ),
                                }
                            ),
                        ]
                    ),
                }
            ),
        },
    )


def test_s3_ingestion_rule_matches_object_created_on_default_bus(
    ingestion_template: assertions.Template,
) -> None:
    """The S3 ingestion rule must match aws.s3 Object Created on the default event bus."""
    ingestion_template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "EventPattern": assertions.Match.object_like(
                {
                    "source": ["aws.s3"],
                    "detail-type": ["Object Created"],
                }
            ),
        },
    )

    template_json = ingestion_template.to_json()
    for _logical_id, resource in template_json.get("Resources", {}).items():
        if resource.get("Type") == "AWS::Events::Rule":
            props = resource.get("Properties", {})
            event_pattern = props.get("EventPattern", {})
            if event_pattern.get("source") == ["aws.s3"]:
                assert "EventBusName" not in props, (
                    "S3 IngestionRule must remain on the default EventBridge bus, "
                    "not on the custom application event bus"
                )


def test_lambda_environment_variables(
    ingestion_template: assertions.Template,
) -> None:
    """The processing Lambda must have the required environment variables."""
    ingestion_template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "AWS_AURORA_CLUSTER_ARN": assertions.Match.any_value(),
                        "AWS_AURORA_SECRET_ARN": assertions.Match.any_value(),
                        "DB_NAME": "cloudguard",
                        "DOCUMENT_STORE": "s3",
                        "EVENT_PUBLISHER": "eventbridge",
                    }
                ),
            },
        },
    )


def test_default_invocation_without_arns_fails() -> None:
    """Default invocation of app.py without ARNs fails fast (secure default)."""
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "app.py"],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert result.returncode != 0
    assert "requires a real 'aurora_cluster_arn'" in result.stderr


def test_dev_without_arns_fails_by_default() -> None:
    """Explicitly setting env=dev without ARNs or offline flag fails fast."""
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "CDK_CONTEXT_JSON": json.dumps({"env": "dev"}),
    }
    result = subprocess.run(
        [sys.executable, "app.py"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "requires a real 'aurora_cluster_arn'" in result.stderr


def test_offline_synth_allows_placeholder_arns_in_dev() -> None:
    """Explicit offline_synth mode in dev allows placeholder ARNs for local synthesis and tests."""
    import os
    import subprocess
    import sys

    # Test via context flag -c offline_synth=true
    env_context = {
        **os.environ,
        "CDK_CONTEXT_JSON": json.dumps({"env": "dev", "offline_synth": "true"}),
    }
    result_context = subprocess.run(
        [sys.executable, "app.py"],
        capture_output=True,
        text=True,
        env=env_context,
    )
    assert result_context.returncode == 0, f"Context offline_synth failed: {result_context.stderr}"

    # Test via environment variable CDK_OFFLINE_SYNTH=1
    env_var = {
        **os.environ,
        "CDK_CONTEXT_JSON": json.dumps({"env": "dev"}),
        "CDK_OFFLINE_SYNTH": "1",
    }
    result_var = subprocess.run(
        [sys.executable, "app.py"],
        capture_output=True,
        text=True,
        env=env_var,
    )
    assert result_var.returncode == 0, f"Env var offline_synth failed: {result_var.stderr}"


def test_real_arns_work_without_offline_mode() -> None:
    """Real ARNs work normally in all environments without requiring any offline flag."""
    import os
    import subprocess
    import sys

    real_arns = {
        "aurora_cluster_arn": "arn:aws:rds:us-east-1:123456789012:cluster:real-cluster",
        "aurora_secret_arn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:real-secret",
    }

    # Dev with real ARNs and no offline flag
    env_dev = {
        **os.environ,
        "CDK_CONTEXT_JSON": json.dumps({"env": "dev", **real_arns}),
    }
    result_dev = subprocess.run(
        [sys.executable, "app.py"],
        capture_output=True,
        text=True,
        env=env_dev,
    )
    assert result_dev.returncode == 0, f"Dev with real ARNs failed: {result_dev.stderr}"

    # Prod with real ARNs
    env_prod = {
        **os.environ,
        "CDK_CONTEXT_JSON": json.dumps({"env": "prod", **real_arns}),
    }
    result_prod = subprocess.run(
        [sys.executable, "app.py"],
        capture_output=True,
        text=True,
        env=env_prod,
    )
    assert result_prod.returncode == 0, f"Prod with real ARNs failed: {result_prod.stderr}"


def test_prod_and_staging_cannot_bypass_arn_validation_with_offline_synth() -> None:
    """Production and staging environments strictly forbid placeholder ARNs even if offline_synth is requested."""
    import os
    import subprocess
    import sys

    for target_env in ("prod", "staging"):
        # Without offline flag
        env_plain = {
            **os.environ,
            "CDK_CONTEXT_JSON": json.dumps({"env": target_env}),
        }
        result_plain = subprocess.run(
            [sys.executable, "app.py"],
            capture_output=True,
            text=True,
            env=env_plain,
        )
        assert result_plain.returncode != 0, f"Expected {target_env} without ARNs to fail"
        assert "requires a real 'aurora_cluster_arn'" in result_plain.stderr

        # With offline_synth=true attempted
        env_offline = {
            **os.environ,
            "CDK_CONTEXT_JSON": json.dumps({"env": target_env, "offline_synth": "true"}),
        }
        result_offline = subprocess.run(
            [sys.executable, "app.py"],
            capture_output=True,
            text=True,
            env=env_offline,
        )
        assert result_offline.returncode != 0, f"Expected {target_env} with offline_synth=true to still fail"
        assert "requires a real 'aurora_cluster_arn'" in result_offline.stderr
