"""Typed application configuration.

Everything the application needs to know about its environment arrives here and
nowhere else. No module reads ``os.environ`` directly — if a value is not in
``Settings``, the application does not use it.

Why this matters beyond tidiness: a mistyped or missing environment variable
fails at *startup*, with a message naming the field, instead of at 2am inside a
Lambda invocation. Pydantic validation is doing the work of a config test suite.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.security.model_budgets import MAX_GENERATION_INPUT_BYTES, MAX_GENERATION_OUTPUT_TOKENS


class Environment(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", extra="ignore")

    host: str = "localhost"
    port: int = 5432
    user: str = "cloudguard"
    # Matches the docker-compose credential so `make dev` works with no setup.
    # Every deployed environment injects DB_PASSWORD from Secrets Manager;
    # nothing outside local ever sees this value.
    password: str = "cloudguard"  # noqa: S105
    name: str = "cloudguard"
    pool_size: int = 5
    echo_sql: bool = False

    @property
    def async_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def sync_dsn(self) -> str:
        """Alembic runs migrations synchronously."""
        return (
            f"postgresql+psycopg2://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class AWSSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AWS_", extra="ignore")

    region: str = "us-east-1"
    documents_bucket: str = "cloudguard-documents-local"
    event_bus_name: str = "cloudguard-events"
    audit_table_name: str = "cloudguard-audit-events"
    aurora_cluster_arn: str | None = None
    aurora_secret_arn: str | None = None
    s3_vector_index_arn: str | None = None
    s3_vector_bucket_name: str | None = None
    s3_vector_index_name: str | None = None
    # Set for LocalStack; leave empty to use real AWS endpoints.
    endpoint_url: str | None = None


class BedrockSettings(BaseSettings):
    """Model identifiers are configuration, never literals in the codebase.

    Model IDs change more often than application code. Hard-coding them turns a
    model upgrade into a code change, a PR, and a deploy.
    """

    model_config = SettingsConfigDict(env_prefix="BEDROCK_", extra="ignore")

    chat_model: str = "anthropic.claude-haiku-4-5-20251001-v1:0"
    reasoning_model: str = "anthropic.claude-sonnet-4-5-20250929-v1:0"
    judge_model: str = "amazon.nova-pro-v1:0"
    embedding_model: str = "amazon.titan-embed-text-v2:0"
    embedding_dimensions: int = 1024
    guardrail_id: str | None = None
    guardrail_version: str | None = None
    max_output_tokens: int = Field(default=2048, ge=1, le=MAX_GENERATION_OUTPUT_TOKENS)
    max_input_bytes: int = Field(
        default=MAX_GENERATION_INPUT_BYTES, ge=1, le=MAX_GENERATION_INPUT_BYTES,
    )
    # Reserved native-token target, not an enforced tokenizer count. The current
    # Nova Pro reviewer does not support CountTokens; byte bounds above are enforced.
    max_input_tokens: int = Field(default=32_000, ge=1, le=32_000)

    @field_validator("guardrail_id", "guardrail_version")
    @classmethod
    def _normalize_guardrail_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _guardrail_configuration_is_complete(self) -> BedrockSettings:
        if bool(self.guardrail_id) != bool(self.guardrail_version):
            raise ValueError(
                "BEDROCK_GUARDRAIL_ID and BEDROCK_GUARDRAIL_VERSION "
                "must be configured together"
            )
        return self


class CognitoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COGNITO_", extra="ignore")

    user_pool_id: str | None = None
    client_id: str | None = None
    region: str = "us-east-1"
    # Hosted UI domain prefix, e.g. "cloudguard-dev". Served to the frontend by
    # /auth/config so rotating a pool does not require rebuilding the SPA.
    hosted_ui_domain: str | None = None

    @property
    def issuer(self) -> str | None:
        if not self.user_pool_id:
            return None
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_uri(self) -> str | None:
        issuer = self.issuer
        return f"{issuer}/.well-known/jwks.json" if issuer else None


class LocalAuthSettings(BaseSettings):
    """Development-only token signing (ADR-0014).

    The default secret is a placeholder and is rejected outside the local
    environment by a validator on Settings. A copied .env cannot silently turn
    local auth on somewhere it does not belong.
    """

    model_config = SettingsConfigDict(env_prefix="LOCAL_AUTH_", extra="ignore")

    # >= 32 bytes to satisfy RFC 7518 for HS256. Placeholder by design: a
    # validator refuses identity_provider="local" outside the local
    # environment, so this value can never sign a token that matters.
    secret: str = "local-development-secret-do-not-use-in-any-real-place"  # noqa: S105
    token_ttl_seconds: int = 3600


class FeatureFlags(BaseSettings):
    """Flags exist so expensive or unfinished paths are off by default.

    Every flag here maps to a phase in the roadmap. A flag that is on in every
    environment forever should be deleted, not kept as decoration.
    """

    model_config = SettingsConfigDict(env_prefix="ENABLE_", extra="ignore")

    agentic_workflows: bool = False      # Phase 6
    reranking: bool = False              # Phase 4+, measured before/after
    advanced_evaluation: bool = False    # Phase 9
    auto_remediation: bool = False       # Future — never default-on
    email_notifications: bool = False    # Future
    automated_reasoning: bool = False    # Phase 8, region-gated


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    app_name: str = "cloudguard-ai"
    api_v1_prefix: str = "/api/v1"

    # None = derive from environment. An explicit value wins, so a dev
    # deployment can turn the OpenAPI surface off without pretending to be prod.
    enable_openapi_docs: bool | None = None

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "console"

    # CORS: exact origins only. "*" is not a valid value in any environment.
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Which adapter implementation to bind to each port. This is the switch that
    # makes local development free — see app/core/container.py.
    identity_provider: Literal["local", "cognito"] = "local"
    llm_provider: Literal["mock", "recorded", "bedrock"] = "mock"
    vector_store: Literal["memory", "pgvector", "s3_vectors"] = "memory"
    document_store: Literal["memory", "s3"] = "memory"
    event_publisher: Literal["memory", "eventbridge"] = "memory"
    # Local uses bounded process memory; every deployed API requires DynamoDB.
    rate_limit_table_name: str | None = Field(default=None, min_length=3, max_length=255)

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    aws: AWSSettings = Field(default_factory=AWSSettings)
    bedrock: BedrockSettings = Field(default_factory=BedrockSettings)
    cognito: CognitoSettings = Field(default_factory=CognitoSettings)
    local_auth: LocalAuthSettings = Field(default_factory=LocalAuthSettings)
    features: FeatureFlags = Field(default_factory=FeatureFlags)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept ``a,b`` from an env var as well as a JSON list."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _validate_origins(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("cors_origins must contain at least one exact origin")

        normalized: list[str] = []
        for origin in value:
            if origin == "*":
                raise ValueError("wildcard CORS origins are not permitted")

            parsed = urlsplit(origin)
            try:
                port = parsed.port
            except ValueError as exc:
                raise ValueError("cors_origins contains an invalid port") from exc

            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or origin != f"{parsed.scheme}://{parsed.netloc}"
                or any(character.isspace() for character in origin)
                or port == 0
            ):
                raise ValueError(
                    "cors_origins entries must be exact http(s) origins without "
                    "credentials, paths, queries, or fragments"
                )
            normalized.append(origin)

        return list(dict.fromkeys(normalized))

    @field_validator("llm_provider")
    @classmethod
    def _no_mock_llm_in_prod(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("environment") is Environment.PROD and value != "bedrock":
            raise ValueError("prod must use the bedrock LLM provider, not a test double")
        return value

    @field_validator("identity_provider")
    @classmethod
    def _local_auth_is_local_only(cls, value: str, info: ValidationInfo) -> str:
        """The single most important validator in this file.

        Development token signing outside the local environment is not a
        misconfiguration to warn about — it is an authentication bypass. The
        process refuses to start.
        """
        if value == "local" and info.data.get("environment") is not Environment.LOCAL:
            raise ValueError(
                "identity_provider='local' is permitted only when environment='local'. "
                "Set IDENTITY_PROVIDER=cognito and configure COGNITO_USER_POOL_ID."
            )
        return value

    @model_validator(mode="after")
    def _cognito_is_fully_configured(self) -> Settings:
        if self.identity_provider == "cognito" and not (
            self.cognito.user_pool_id and self.cognito.client_id
        ):
            raise ValueError(
                "identity_provider='cognito' requires COGNITO_USER_POOL_ID "
                "and COGNITO_CLIENT_ID"
            )

        if self.environment is Environment.PROD and any(
            urlsplit(origin).scheme != "https" for origin in self.cors_origins
        ):
            raise ValueError("prod CORS origins must use https")
        if self.environment is Environment.PROD and not (
            self.bedrock.guardrail_id and self.bedrock.guardrail_version
        ):
            raise ValueError(
                "prod Bedrock model generation requires a configured guardrail"
            )
        return self

    @property
    def is_local(self) -> bool:
        return self.environment is Environment.LOCAL

    @property
    def docs_enabled(self) -> bool:
        """OpenAPI docs are a reconnaissance surface. Off in prod by default."""
        if self.enable_openapi_docs is not None:
            return self.enable_openapi_docs
        return self.environment is not Environment.PROD


class WorkerSettings(BaseSettings):
    """Scoped configuration for background and deployed document-processing workers.

    Workers only need document storage, event publishing, Data API persistence, and
    logging configuration. They do not run the HTTP API, do not sign or verify JWTs,
    and must not require Cognito settings.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "console"
    document_store: Literal["memory", "s3"] = "memory"
    event_publisher: Literal["memory", "eventbridge"] = "memory"
    vector_store: Literal["memory", "pgvector", "s3_vectors"] = "memory"
    llm_provider: Literal["mock", "recorded", "bedrock"] = "mock"
    rate_limit_table_name: str | None = Field(default=None, min_length=3, max_length=255)

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    aws: AWSSettings = Field(default_factory=AWSSettings)
    bedrock: BedrockSettings = Field(default_factory=BedrockSettings)


class AgentWorkerSettings(BaseSettings):
    """Scoped configuration for deployed Phase 6 agent workflow tasks.

    Agent workers consume an already authenticated Principal from trusted workflow
    state. They therefore need model, retrieval, and feature-gate configuration,
    but must not require API identity-provider or Cognito configuration.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "console"
    llm_provider: Literal["mock", "recorded", "bedrock"] = "mock"
    vector_store: Literal["memory", "pgvector", "s3_vectors"] = "memory"
    rate_limit_table_name: str | None = Field(default=None, min_length=3, max_length=255)

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    aws: AWSSettings = Field(default_factory=AWSSettings)
    bedrock: BedrockSettings = Field(default_factory=BedrockSettings)
    features: FeatureFlags = Field(default_factory=FeatureFlags)

    @field_validator("llm_provider")
    @classmethod
    def _no_test_llm_in_prod(cls, value: str, info: ValidationInfo) -> str:
        """Production agent workers must use the real Bedrock provider."""
        if info.data.get("environment") is Environment.PROD and value != "bedrock":
            raise ValueError(
                "prod agent workers must use the bedrock LLM provider, not a test double"
            )
        return value

    @model_validator(mode="after")
    def _production_guardrail_is_required(self) -> AgentWorkerSettings:
        if self.environment is Environment.PROD and not (
            self.bedrock.guardrail_id and self.bedrock.guardrail_version
        ):
            raise ValueError(
                "prod Bedrock model generation requires a configured guardrail"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached so the process parses and validates the environment exactly once."""
    return Settings()


@lru_cache
def get_worker_settings() -> WorkerSettings:
    """Cached configuration for worker tasks."""
    return WorkerSettings()


@lru_cache
def get_agent_worker_settings() -> AgentWorkerSettings:
    """Cached configuration for deployed agent workflow tasks."""
    return AgentWorkerSettings()
