"""Configuration must fail loudly and early."""

from __future__ import annotations

import pytest
from app.core.config import Environment, Settings, WorkerSettings
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
    settings = Settings(cors_origins="http://a.test, http://b.test")  # type: ignore[arg-type]
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_database_dsn_is_built_from_parts() -> None:
    settings = Settings()
    assert settings.database.async_dsn.startswith("postgresql+asyncpg://")
    assert settings.database.sync_dsn.startswith("postgresql+psycopg2://")


def test_cognito_issuer_is_none_until_configured() -> None:
    assert Settings().cognito.issuer is None
