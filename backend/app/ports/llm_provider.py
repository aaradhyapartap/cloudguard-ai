"""Port: language model inference.

The rule this enforces: **nothing above this line knows what a Bedrock is.**
``rag/``, ``agents/`` and ``services/`` depend on this interface only.

Three implementations exist by design (ADR-0013):

* ``mock``     — deterministic canned output. Unit tests, offline work. Free.
* ``recorded`` — replays cassettes captured from real Bedrock calls. Integration
                 tests get realistic output with byte-identical determinism. Free
                 after the first recording.
* ``bedrock``  — real inference. Phase 4.

The middle one is the reason this port earns its keep. LLM output is not
deterministic, non-deterministic tests get disabled, and disabled tests rot.
Cassettes make the AI pipeline testable in CI and refactorable at 1am for $0.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.ai import EmbeddingResult, GenerationRequest, GenerationResponse


class LLMProvider(ABC):
    """Text generation and embedding."""

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Generate a completion.

        Implementations must populate ``usage`` even when it has to be estimated.
        Token accounting that is sometimes absent is token accounting nobody
        trusts, and cost-per-query is a headline metric for this project.
        """

    @abstractmethod
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed one or more texts. Order of ``vectors`` matches order of ``texts``."""

    @property
    @abstractmethod
    def chat_model_id(self) -> str:
        """Recorded on every AuditEvent, so a past decision can be reproduced."""

    @property
    @abstractmethod
    def embedding_model_id(self) -> str:
        """Stored per chunk, so a model upgrade becomes a detectable reindex."""
