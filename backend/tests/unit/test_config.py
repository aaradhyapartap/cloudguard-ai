"""Configuration must fail loudly and early."""

from __future__ import annotations

import pytest
from app.core.config import (
    AgentWorkerSettings,
    ApprovalWorkerSettings,
    Environment,
    Settings,
    WorkerSettings,
)
from pydantic import ValidationError


def test_defaults_are_safe_for_local_development() -> None:
    settings = Settings()
    assert settings.environment is Environment.LOCAL
    assert settings.llm_provider == "mock"
    assert settings.identity_provider == "local"
    assert settings.docs_enabled is True


def test_worker_settings_safe_without_identity() -> None:
    """Worker settings do not require Cognito or identity provider."""
    worker = WorkerSettings(environment=Environment.DEV)
    assert worker.environment is Environment.DEV
    assert worker.document_store == "memory"
    assert worker.event_publisher == "memory"


def test_agent_worker_settings_are_identity_free_and_feature_gated() -> None:
    """Agent workflow tasks do not require API identity configuration."""
    worker = AgentWorkerSettings(environment=Environment.DEV)
    assert worker.environment is Environment.DEV
    assert worker.llm_provider == "mock"
    assert worker.vector_store == "memory"
    assert worker.features.agentic_workflows is False


def test_production_agent_worker_rejects_mock_llm() -> None:
    """Production agent tasks must never silently use the mock provider."""
    with pytest.raises(
        ValidationError,
        match="prod agent workers must use the bedrock LLM provider",
    ):
        AgentWorkerSettings(
            environment=Environment.PROD,
            llm_provider="mock",
        )


def test_production_agent_worker_rejects_recorded_llm() -> None:
    """Recorded model responses are also forbidden in production agent tasks."""
    with pytest.raises(
        ValidationError,
        match="prod agent workers must use the bedrock LLM provider",
    ):
        AgentWorkerSettings(
            environment=Environment.PROD,
            llm_provider="recorded",
        )


def test_production_agent_worker_accepts_bedrock_llm() -> None:
    """Bedrock is the only valid production agent model provider."""
    worker = AgentWorkerSettings(
        environment=Environment.PROD,
        llm_provider="bedrock",
        bedrock={"guardrail_id": "gr-production", "guardrail_version": "1"},
    )

    assert worker.llm_provider == "bedrock"


def test_dev_api_requires_cognito_or_fails() -> None:
    """API Settings in dev environment default to local auth and fail without Cognito."""
    with pytest.raises(ValidationError, match="permitted only when environment='local'"):
        Settings(environment=Environment.DEV)


def test_production_rejects_a_test_double_llm() -> None:
    """A mock provider reaching production is a silent, expensive failure."""
    with pytest.raises(ValidationError, match="prod must use the bedrock LLM provider"):
        Settings(
            environment=Environment.PROD,
            llm_provider="mock",
            identity_provider="cognito",
            cognito={"user_pool_id": "us-east-1_Pool", "client_id": "c"},
        )


def production_settings(**overrides: object) -> Settings:
    """A minimally valid production configuration."""
    base: dict[str, object] = {
        "environment": Environment.PROD,
        "llm_provider": "bedrock",
        "identity_provider": "cognito",
        "cognito": {"user_pool_id": "us-east-1_Pool", "client_id": "client-123"},
        "cors_origins": ["https://console.cloudguard.example"],
        "bedrock": {"guardrail_id": "gr-production", "guardrail_version": "1"},
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_production_disables_openapi_docs() -> None:
    assert production_settings().docs_enabled is False


def test_local_identity_provider_is_refused_outside_local() -> None:
    """The most important validator in the file.

    Development token signing outside `local` is not a misconfiguration to warn
    about — it is an authentication bypass, so the process refuses to start.
    """
    with pytest.raises(ValidationError, match="permitted only when environment='local'"):
        production_settings(identity_provider="local")


def test_dev_environment_also_refuses_local_identity() -> None:
    with pytest.raises(ValidationError, match="permitted only when environment='local'"):
        Settings(environment=Environment.DEV, identity_provider="local")


def test_cognito_requires_pool_and_client_id() -> None:
    with pytest.raises(ValidationError, match="COGNITO_USER_POOL_ID"):
        Settings(
            environment=Environment.DEV,
            identity_provider="cognito",
            llm_provider="mock",
        )


def test_cognito_issuer_is_derived_from_pool() -> None:
    settings = production_settings()
    assert settings.cognito.issuer == (
        "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Pool"
    )


def test_cors_origins_accept_a_comma_separated_env_value() -> None:
    settings = Settings(cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


@pytest.mark.parametrize(
    "origins",
    [
        [],
        ["*"],
        ["https://console.example/path"],
        ["https://user:password@console.example"],
        ["https://console.example:not-a-port"],
        ["https://console example"],
        ["javascript:alert(1)"],
    ],
)
def test_cors_origins_reject_non_origin_values(origins: list[str]) -> None:
    with pytest.raises(ValidationError, match=r"cors_origins|CORS origins"):
        Settings(cors_origins=origins)


def test_cors_origins_are_deduplicated() -> None:
    settings = Settings(
        cors_origins=["https://console.example", "https://console.example"]
    )
    assert settings.cors_origins == ["https://console.example"]


def test_production_rejects_insecure_cors_origin() -> None:
    with pytest.raises(ValidationError, match="prod CORS origins must use https"):
        production_settings(cors_origins=["http://console.cloudguard.example"])


@pytest.mark.parametrize(
    "bedrock",
    [
        {"guardrail_id": "gr-only"},
        {"guardrail_version": "1"},
        {"guardrail_id": "   ", "guardrail_version": "1"},
        {"guardrail_id": "gr-only", "guardrail_version": "   "},
    ],
)
def test_guardrail_id_and_version_must_be_configured_together(
    bedrock: dict[str, str],
) -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(bedrock=bedrock)


def test_production_api_requires_bedrock_guardrail() -> None:
    with pytest.raises(ValidationError, match="requires a configured guardrail"):
        production_settings(bedrock={})


def test_production_agent_worker_requires_bedrock_guardrail() -> None:
    with pytest.raises(ValidationError, match="requires a configured guardrail"):
        AgentWorkerSettings(
            environment=Environment.PROD,
            llm_provider="bedrock",
        )


def test_database_dsn_is_built_from_parts() -> None:
    settings = Settings()
    assert settings.database.async_dsn.startswith("postgresql+asyncpg://")
    assert settings.database.sync_dsn.startswith("postgresql+psycopg2://")


def test_cognito_issuer_is_none_until_configured() -> None:
    assert Settings().cognito.issuer is None


def staging_settings(**overrides: object) -> Settings:
    """A minimally valid staging API configuration."""
    base: dict[str, object] = {
        "environment": Environment.STAGING,
        "llm_provider": "bedrock",
        "identity_provider": "cognito",
        "cognito": {"user_pool_id": "us-east-1_StagingPool", "client_id": "client-stg"},
        "cors_origins": ["https://staging.cloudguard.example"],
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_staging_environment_accepts_secure_configuration() -> None:
    settings = staging_settings()
    assert settings.environment is Environment.STAGING
    assert settings.docs_enabled is False
    assert settings.is_local is False


def test_staging_rejects_local_identity_provider() -> None:
    with pytest.raises(ValidationError, match="permitted only when environment='local'"):
        staging_settings(identity_provider="local")


@pytest.mark.parametrize("provider", ["mock", "recorded"])
def test_staging_rejects_test_double_llm_across_settings_and_workers(
    provider: str,
) -> None:
    with pytest.raises(ValidationError, match="staging must use the bedrock LLM provider"):
        staging_settings(llm_provider=provider)

    with pytest.raises(
        ValidationError, match="staging document workers must use the bedrock LLM provider"
    ):
        WorkerSettings(environment=Environment.STAGING, llm_provider=provider)  # type: ignore[arg-type]

    with pytest.raises(
        ValidationError, match="staging agent workers must use the bedrock LLM provider"
    ):
        AgentWorkerSettings(environment=Environment.STAGING, llm_provider=provider)  # type: ignore[arg-type]


def test_staging_rejects_insecure_cors_origin() -> None:
    with pytest.raises(ValidationError, match="staging CORS origins must use https"):
        staging_settings(cors_origins=["http://staging.cloudguard.example"])


def test_deployed_s3_ingestion_rejects_mock_embeddings() -> None:
    for env in (Environment.DEV, Environment.STAGING, Environment.PROD):
        with pytest.raises(
            ValidationError,
            match=r"bedrock LLM provider|requires LLM_PROVIDER='bedrock'",
        ):
            WorkerSettings(
                environment=env,
                document_store="s3",
                llm_provider="mock",
            )


def test_approval_worker_settings_start_without_cognito_cors_or_guardrail() -> None:
    for env in (Environment.DEV, Environment.STAGING, Environment.PROD):
        settings = ApprovalWorkerSettings(
            environment=env,
            aws={
                "aurora_cluster_arn": "arn:aws:rds:us-east-1:111122223333:cluster:cg",
                "aurora_secret_arn": "arn:aws:secretsmanager:us-east-1:111122223333:secret:cg",
            },
            features={"agentic_workflows": True},
        )
        assert settings.environment is env
        assert settings.features.agentic_workflows is True


def test_synthesized_worker_environments_validate_across_dev_staging_and_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthesized Lambda environment contracts for dev/staging/prod validate in worker settings."""
    from app.adapters.bedrock.embedding import BedrockEmbeddingProvider
    from app.core.container import build_worker_container

    for env_name in ("dev", "staging", "prod"):
        ing_vars = {
            "RATE_LIMIT_TABLE_NAME": f"cloudguard-api-rate-limits-{env_name}",
            "ENVIRONMENT": env_name,
            "AWS_AURORA_CLUSTER_ARN": f"arn:aws:rds:us-east-1:123456789012:cluster:cg-{env_name}",
            "AWS_AURORA_SECRET_ARN": (
                f"arn:aws:secretsmanager:us-east-1:123456789012:secret:cg-{env_name}"
            ),
            "DB_NAME": "cloudguard",
            "DOCUMENT_STORE": "s3",
            "AWS_DOCUMENTS_BUCKET": f"cloudguard-documents-{env_name}-123456789012",
            "EVENT_PUBLISHER": "eventbridge",
            "AWS_EVENT_BUS_NAME": "cloudguard-events",
            "LLM_PROVIDER": "bedrock",
            "BEDROCK_EMBEDDING_MODEL": "amazon.titan-embed-text-v2:0",
            "VECTOR_STORE": "pgvector",
        }

        with monkeypatch.context() as mp:
            for key in (
                "IDENTITY_PROVIDER",
                "COGNITO_USER_POOL_ID",
                "COGNITO_CLIENT_ID",
                "CORS_ORIGINS",
                "BEDROCK_GUARDRAIL_ID",
                "BEDROCK_GUARDRAIL_VERSION",
            ):
                mp.delenv(key, raising=False)
            for k, v in ing_vars.items():
                mp.setenv(k, v)
            ws = WorkerSettings()
            assert ws.environment is Environment(env_name)
            assert ws.llm_provider == "bedrock"
            assert ws.bedrock.embedding_model == "amazon.titan-embed-text-v2:0"
            container = build_worker_container(ws)
            assert isinstance(container.embeddings, BedrockEmbeddingProvider)

        common_wf_vars = {
            "ENVIRONMENT": env_name,
            "LLM_PROVIDER": "bedrock",
            "ENABLE_AGENTIC_WORKFLOWS": "true",
            "VECTOR_STORE": "pgvector",
            "AWS_AURORA_CLUSTER_ARN": f"arn:aws:rds:us-east-1:123456789012:cluster:cg-{env_name}",
            "AWS_AURORA_SECRET_ARN": (
                f"arn:aws:secretsmanager:us-east-1:123456789012:secret:cg-{env_name}"
            ),
            "DB_NAME": "cloudguard",
        }
        if env_name == "prod":
            common_wf_vars["BEDROCK_GUARDRAIL_ID"] = "gr-prod-123"
            common_wf_vars["BEDROCK_GUARDRAIL_VERSION"] = "1"

        worker_envs = {
            "app.deployed_research_agent_worker.handler": {
                **common_wf_vars,
                "BEDROCK_CHAT_MODEL": "anthropic.claude-haiku-4-5-20251001-v1:0",
                "BEDROCK_EMBEDDING_MODEL": "amazon.titan-embed-text-v2:0",
                "RATE_LIMIT_TABLE_NAME": f"cloudguard-api-rate-limits-{env_name}",
            },
            "app.deployed_risk_agent_worker.handler": {
                **common_wf_vars,
                "BEDROCK_CHAT_MODEL": "anthropic.claude-haiku-4-5-20251001-v1:0",
                "RATE_LIMIT_TABLE_NAME": f"cloudguard-api-rate-limits-{env_name}",
            },
            "app.deployed_reviewer_agent_worker.handler": {
                **common_wf_vars,
                "BEDROCK_JUDGE_MODEL": "amazon.nova-pro-v1:0",
                "RATE_LIMIT_TABLE_NAME": f"cloudguard-api-rate-limits-{env_name}",
            },
            "app.deployed_approval_creation_worker.handler": dict(common_wf_vars),
            "app.deployed_approval_callback_recovery_worker.handler": dict(common_wf_vars),
            "app.deployed_approved_action_execution_worker.handler": dict(common_wf_vars),
        }

        for cmd, fn_vars in worker_envs.items():
            with monkeypatch.context() as mp:
                for key in (
                    "IDENTITY_PROVIDER",
                    "COGNITO_USER_POOL_ID",
                    "COGNITO_CLIENT_ID",
                    "CORS_ORIGINS",
                    "BEDROCK_GUARDRAIL_ID",
                    "BEDROCK_GUARDRAIL_VERSION",
                ):
                    mp.delenv(key, raising=False)
                for k, v in fn_vars.items():
                    mp.setenv(k, v)

                if cmd in (
                    "app.deployed_approval_creation_worker.handler",
                    "app.deployed_approval_callback_recovery_worker.handler",
                    "app.deployed_approved_action_execution_worker.handler",
                ):
                    mp.delenv("BEDROCK_GUARDRAIL_ID", raising=False)
                    mp.delenv("BEDROCK_GUARDRAIL_VERSION", raising=False)
                    mp.delenv("LLM_PROVIDER", raising=False)
                    aws_settings = ApprovalWorkerSettings()
                    assert aws_settings.environment is Environment(env_name)
                    assert aws_settings.features.agentic_workflows is True
                else:
                    agent_settings = AgentWorkerSettings()
                    assert agent_settings.environment is Environment(env_name)
                    assert agent_settings.llm_provider == "bedrock"
