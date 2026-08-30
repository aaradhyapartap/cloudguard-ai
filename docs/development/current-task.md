# Current Development Task

## Phase

Phase 4 - Retrieval-Augmented Generation (Completed) / Phase 5 - Deterministic Compliance & Risk Scoring (Planning)

## Branch

phase-4-rag

## Phase 4 Status

Phase 4 - Retrieval-Augmented Generation is complete, verified, and audited.

Completed capabilities include:

- **pgvector Persistence Foundation (Phase 4.1)**:
  - Alembic migration enabling `vector` extension and adding `embedding vector(1024)` column to `document_chunks`.
  - HNSW index using `vector_cosine_ops`.
  - Local `SQLAlchemyVectorStore` with pgvector cosine distance operations and RLS enforcement.
  - Deployed `AuroraDataAPIVectorStore` using parameterized `:embedding::vector` SQL.
  - Safe vector clearing (`delete_by_document` nulls embedding while preserving chunk rows).
- **Embedding Generation & Ingestion Lifecycle (Phase 4.2)**:
  - Independent `EmbeddingProvider` and `LLMProvider` abstractions.
  - Dedicated `BedrockEmbeddingProvider` for Amazon Titan Text Embeddings v2 (`amazon.titan-embed-text-v2:0`).
  - Strict ingestion lifecycle: `QUEUED -> EXTRACTING -> INDEXING -> READY`.
  - Documents transition to `READY` and publish `DocumentIndexed` only after vector persistence succeeds.
  - Concurrency-safe atomic claims (`claim_for_processing` and `claim_for_indexing`).
- **Retrieval Orchestration (Phase 4.3)**:
  - `RetrievalService` composing `EmbeddingProvider` and `VectorStore`.
  - Strict tenant scoping from `Principal.organization_id`.
  - Role-based confidentiality clearance ceiling mapping (Analyst = INTERNAL, Manager = CONFIDENTIAL, Admin = RESTRICTED).
  - API endpoint `POST /api/v1/retrieval/search` guarded by `DOCUMENT_READ`.
- **RAG Answer Generation (Phase 4.4)**:
  - `RAGService` composing `RetrievalService` and `LLMProvider`.
  - Prompt-injection resistant system prompt treating document context as untrusted reference data.
  - Deterministic source citations (`[S1]`, `[S2]`) with authoritative `RAGSource` mapping.
  - Hard upper bound enforced on reference context length with deterministic content truncation on first-chunk overflow.
  - Zero-retrieval results skip LLM generation.
  - `BedrockLLMProvider` Converse API adapter with fail-closed response validation.
  - Stable, provider-neutral error normalization (`UpstreamError`).
  - API endpoint `POST /api/v1/rag/query` guarded by `DOCUMENT_READ`.

Phase 4 final validation:

- Ruff: passed
- mypy: passed (78 source files)
- backend tests: 375 passed with RLS role
- infrastructure tests: 21 passed
- git diff --check: passed

## Current Next Task

Prepare Phase 5 — Deterministic Compliance & Risk Scoring architecture review before implementation.

Phase 5 goals:
- Establish compliance evaluation and risk scoring models.
- Implement deterministic Python scoring engine (versioned `scoring_version`).
- Keep LLM role bounded to extracting structured estimates while code computes the risk score.
- Create domain events and audit trails for compliance evaluations.

## Before Editing Phase 5

Read:
- AGENTS.md
- docs/architecture/00-phase0-architecture.md (§E.4, §P ADR 0005)
- docs/adr/
- backend/app/models/
- backend/app/services/

## Validation

Full project validation:

powershell -ExecutionPolicy Bypass -File .\scripts\validate-all.ps1

## Commit Policy

Do not commit or push until:

- focused validation passes
- full validation passes
- git diff --check passes
- architecture review is complete
