"""The composition root binds ports to adapters, and defers clearly."""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.core.container import AdapterNotAvailableError, build_container
from app.ports.llm_provider import LLMProvider
from app.ports.vector_store import VectorStore


def test_local_defaults_bind_test_doubles(settings: Settings) -> None:
    container = build_container(settings)
    assert isinstance(container.llm, LLMProvider)
    assert isinstance(container.vectors, VectorStore)


def test_unimplemented_adapter_names_the_phase_that_adds_it() -> None:
    with pytest.raises(AdapterNotAvailableError, match="Phase 4"):
        build_container(Settings(vector_store="pgvector"))


def test_s3_vectors_is_deferred_to_phase_11() -> None:
    with pytest.raises(AdapterNotAvailableError, match="Phase 11"):
        build_container(Settings(vector_store="s3_vectors"))
