"""Composition root.

This is the only module that decides which concrete adapter satisfies which
port. Everything else asks for the interface and is handed whatever the
environment configured.

Why a container rather than importing adapters where they are used: it makes the
dependency graph a single readable file, and it makes ``LLM_PROVIDER=mock`` a
one-line environment change instead of a code change. Running the entire
application against test doubles is what makes local development free — the
strategy in §K of the Phase 0 document is implemented right here.

Adapters not yet built raise a clear, actionable error naming the phase that
adds them. That is a deliberate deferral, not an unfinished stub: it fails at
startup with a sentence you can act on, rather than at request time with an
``AttributeError``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.cognito.identity import CognitoIdentityProvider
from app.adapters.local.identity import LocalIdentityProvider
from app.adapters.mock.document_store import InMemoryDocumentStore
from app.adapters.mock.embedding import MockEmbeddingProvider
from app.adapters.mock.event_publisher import InMemoryEventPublisher
from app.adapters.mock.llm import MockLLMProvider
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.core.config import (
    Environment,
    Settings,
    WorkerSettings,
    get_settings,
    get_worker_settings,
)
from app.core.logging import get_logger
from app.ports.document_store import DocumentStore
from app.ports.event_publisher import EventPublisher
from app.ports.identity_provider import IdentityProvider
from app.ports.llm_provider import EmbeddingProvider, LLMProvider
from app.ports.vector_store import VectorStore

logger = get_logger(__name__)


class AdapterNotAvailableError(RuntimeError):
    """Raised at startup when configuration selects an adapter that does not exist yet."""

    def __init__(self, port: str, selection: str, phase: str) -> None:
        super().__init__(
            f"{port} adapter {selection!r} is not implemented yet (arrives in {phase}). "
            f"Set the corresponding environment variable to a supported value."
        )


@dataclass(frozen=True, slots=True)
class Container:
    """Every dependency the application layer is allowed to reach for."""

    settings: Settings
    identity: IdentityProvider
    llm: LLMProvider
    reviewer_llm: LLMProvider
    embeddings: EmbeddingProvider
    vectors: VectorStore
    documents: DocumentStore
    events: EventPublisher


def _build_identity(settings: Settings) -> IdentityProvider:
    match settings.identity_provider:
        case "local":
            # Settings validation has already refused this outside `local`.
            return LocalIdentityProvider(
                secret=settings.local_auth.secret,
                token_ttl_seconds=settings.local_auth.token_ttl_seconds,
            )
        case "cognito":
            # Both values are guaranteed present by the Settings model validator.
            assert settings.cognito.user_pool_id is not None
            assert settings.cognito.client_id is not None
            return CognitoIdentityProvider(
                user_pool_id=settings.cognito.user_pool_id,
                client_id=settings.cognito.client_id,
                region=settings.cognito.region,
            )
        case _:
            raise ValueError(f"Unknown identity provider: {settings.identity_provider}")


def _build_embeddings(settings: Settings | WorkerSettings) -> EmbeddingProvider:
    match settings.llm_provider:
        case "mock":
            return MockEmbeddingProvider(dimensions=settings.bedrock.embedding_dimensions)
        case "recorded":
            raise AdapterNotAvailableError("EmbeddingProvider", "recorded", "Phase 4")
        case "bedrock":
            from app.adapters.bedrock.embedding import BedrockEmbeddingProvider

            return BedrockEmbeddingProvider(
                embedding_model_id=settings.bedrock.embedding_model,
                dimensions=settings.bedrock.embedding_dimensions,
                region=settings.aws.region,
                endpoint_url=settings.aws.endpoint_url,
            )


def _build_llm(settings: Settings) -> LLMProvider:
    match settings.llm_provider:
        case "mock":
            return MockLLMProvider()
        case "recorded":
            raise AdapterNotAvailableError("LLMProvider", "recorded", "Phase 4")
        case "bedrock":
            from app.adapters.bedrock.llm import BedrockLLMProvider

            return BedrockLLMProvider(
                chat_model_id=settings.bedrock.chat_model,
                region=settings.aws.region,
                endpoint_url=settings.aws.endpoint_url,
                guardrail_id=settings.bedrock.guardrail_id,
                guardrail_version=settings.bedrock.guardrail_version,
                max_output_tokens=settings.bedrock.max_output_tokens,
            )


def _build_reviewer_llm(settings: Settings) -> LLMProvider:
    """Build the independently configured Reviewer/Judge model provider."""
    match settings.llm_provider:
        case "mock":
            return MockLLMProvider()
        case "recorded":
            raise AdapterNotAvailableError("LLMProvider", "recorded", "Phase 4")
        case "bedrock":
            from app.adapters.bedrock.llm import BedrockLLMProvider

            return BedrockLLMProvider(
                chat_model_id=settings.bedrock.judge_model,
                region=settings.aws.region,
                endpoint_url=settings.aws.endpoint_url,
                guardrail_id=settings.bedrock.guardrail_id,
                guardrail_version=settings.bedrock.guardrail_version,
                max_output_tokens=settings.bedrock.max_output_tokens,
            )


def _build_vector_store(settings: Settings | WorkerSettings) -> VectorStore:
    match settings.vector_store:
        case "memory":
            return InMemoryVectorStore()
        case "pgvector":
            if settings.environment == Environment.LOCAL:
                from app.adapters.local.vector_store import SQLAlchemyVectorStore

                return SQLAlchemyVectorStore()
            else:
                from app.adapters.aws.vector_store import AuroraDataAPIVectorStore

                cluster_arn = settings.aws.aurora_cluster_arn
                secret_arn = settings.aws.aurora_secret_arn
                if not cluster_arn or not secret_arn:
                    raise ValueError(
                        "AWS_AURORA_CLUSTER_ARN and AWS_AURORA_SECRET_ARN are "
                        "required when vector_store='pgvector' in deployed environments."
                    )
                return AuroraDataAPIVectorStore(
                    resource_arn=cluster_arn,
                    secret_arn=secret_arn,
                    database=settings.database.name,
                    region=settings.aws.region,
                    endpoint_url=settings.aws.endpoint_url,
                )
        case "s3_vectors":
            raise AdapterNotAvailableError("VectorStore", "s3_vectors", "Phase 11")


def _build_document_store(settings: Settings | WorkerSettings) -> DocumentStore:
    match settings.document_store:
        case "memory":
            return InMemoryDocumentStore()
        case "s3":
            from app.adapters.aws.document_store import S3DocumentStore

            return S3DocumentStore(
                bucket=settings.aws.documents_bucket,
                region=settings.aws.region,
                endpoint_url=settings.aws.endpoint_url,
            )


def _build_event_publisher(settings: Settings | WorkerSettings) -> EventPublisher:
    match settings.event_publisher:
        case "memory":
            return InMemoryEventPublisher()
        case "eventbridge":
            from app.adapters.aws.event_publisher import EventBridgePublisher

            return EventBridgePublisher(
                bus_name=settings.aws.event_bus_name,
                region=settings.aws.region,
                endpoint_url=settings.aws.endpoint_url,
            )


def build_container(settings: Settings | None = None) -> Container:
    resolved = settings or get_settings()
    container = Container(
        settings=resolved,
        identity=_build_identity(resolved),
        llm=_build_llm(resolved),
        reviewer_llm=_build_reviewer_llm(resolved),
        embeddings=_build_embeddings(resolved),
        vectors=_build_vector_store(resolved),
        documents=_build_document_store(resolved),
        events=_build_event_publisher(resolved),
    )
    logger.info(
        "container_built",
        environment=resolved.environment.value,
        identity_provider=resolved.identity_provider,
        llm_provider=resolved.llm_provider,
        vector_store=resolved.vector_store,
        document_store=resolved.document_store,
        event_publisher=resolved.event_publisher,
    )
    return container


@dataclass(frozen=True, slots=True)
class WorkerContainer:
    """Every dependency the worker layer is allowed to reach for."""

    settings: Settings | WorkerSettings
    documents: DocumentStore
    events: EventPublisher
    vectors: VectorStore
    embeddings: EmbeddingProvider


def build_worker_container(
    settings: Settings | WorkerSettings | None = None,
) -> WorkerContainer:
    resolved = settings or get_worker_settings()
    container = WorkerContainer(
        settings=resolved,
        documents=_build_document_store(resolved),
        events=_build_event_publisher(resolved),
        vectors=_build_vector_store(resolved),
        embeddings=_build_embeddings(resolved),
    )
    logger.info(
        "worker_container_built",
        environment=resolved.environment.value,
        document_store=resolved.document_store,
        event_publisher=resolved.event_publisher,
        vector_store=resolved.vector_store,
        embedding_provider=resolved.llm_provider,
    )
    return container
