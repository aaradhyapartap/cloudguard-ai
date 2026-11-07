"""The test doubles must be deterministic and must enforce the same filters."""

from __future__ import annotations

from uuid import uuid4

from app.adapters.mock.embedding import MockEmbeddingProvider
from app.adapters.mock.event_publisher import InMemoryEventPublisher
from app.adapters.mock.llm import MockLLMProvider
from app.adapters.mock.vector_store import InMemoryVectorStore
from app.models.ai import GenerationRequest, Message, VectorRecord
from app.models.enums import ConfidentialityLevel


async def test_mock_llm_is_deterministic() -> None:
    provider = MockLLMProvider()
    request = GenerationRequest(messages=[Message(role="user", content="policy?")])
    first = await provider.generate(request)
    second = await provider.generate(request)
    assert first.content == second.content


async def test_mock_llm_reports_usage() -> None:
    provider = MockLLMProvider()
    response = await provider.generate(
        GenerationRequest(messages=[Message(role="user", content="a" * 400)])
    )
    assert response.usage.input_tokens > 0
    assert response.usage.total_tokens == (
        response.usage.input_tokens + response.usage.output_tokens
    )


async def test_mock_embeddings_are_stable_and_normalised() -> None:
    provider = MockEmbeddingProvider(dimensions=32)
    first = await provider.embed(["vendor access review"])
    second = await provider.embed(["vendor access review"])
    assert first.vectors == second.vectors
    magnitude = sum(value * value for value in first.vectors[0]) ** 0.5
    assert abs(magnitude - 1.0) < 1e-9


async def test_vector_store_refuses_cross_tenant_results() -> None:
    """The double enforces the same isolation as the real adapters.

    A permissive test double lets a cross-tenant bug pass CI and fail in AWS.
    """
    store = InMemoryVectorStore()
    org_a, org_b = uuid4(), uuid4()
    await store.upsert(
        [
            VectorRecord(
                chunk_id="a-1",
                document_id="doc-a",
                organization_id=str(org_a),
                embedding=[1.0, 0.0],
                content="tenant A policy",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="b-1",
                document_id="doc-b",
                organization_id=str(org_b),
                embedding=[1.0, 0.0],
                content="tenant B policy",
                metadata={"confidentiality_level": "internal"},
            ),
        ]
    )

    matches = await store.search(
        embedding=[1.0, 0.0],
        organization_id=org_a,
        confidentiality_levels=(
            ConfidentialityLevel.PUBLIC,
            ConfidentialityLevel.INTERNAL,
        ),
        top_k=10,
    )
    assert [match.chunk_id for match in matches] == ["a-1"]


async def test_vector_store_applies_the_clearance_filter() -> None:
    store = InMemoryVectorStore()
    org = uuid4()
    await store.upsert(
        [
            VectorRecord(
                chunk_id="open",
                document_id="d1",
                organization_id=str(org),
                embedding=[1.0, 0.0],
                content="internal",
                metadata={"confidentiality_level": "internal"},
            ),
            VectorRecord(
                chunk_id="secret",
                document_id="d2",
                organization_id=str(org),
                embedding=[1.0, 0.0],
                content="restricted",
                metadata={"confidentiality_level": "restricted"},
            ),
        ]
    )
    matches = await store.search(
        embedding=[1.0, 0.0],
        organization_id=org,
        confidentiality_levels=(
            ConfidentialityLevel.PUBLIC,
            ConfidentialityLevel.INTERNAL,
        ),
    )
    assert [match.chunk_id for match in matches] == ["open"]


async def test_event_publisher_records_what_was_published() -> None:
    from datetime import UTC, datetime

    from app.models.ai import DomainEvent

    publisher = InMemoryEventPublisher()
    await publisher.publish(
        DomainEvent(
            event_type="DocumentUploaded",
            organization_id=str(uuid4()),
            payload={"document_id": "d1"},
            occurred_at=datetime.now(UTC),
        )
    )
    assert [event.event_type for event in publisher.events] == ["DocumentUploaded"]
