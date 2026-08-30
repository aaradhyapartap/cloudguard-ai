# Current Development Task

## Phase

Phase 4 - Retrieval-Augmented Generation

## Branch

phase-3-document-ingestion

## Phase 3 status

Phase 3 - Document Ingestion is complete and pushed.

Completed capabilities include:

- document registration
- presigned S3 upload flow
- upload completion verification
- S3 ObjectCreated -> EventBridge -> Step Functions Standard orchestration
- deployed Data API document-processing Lambda
- local SQLAlchemy worker path
- Aurora Data API processing repository
- atomic QUEUED -> EXTRACTING processing claim
- concurrent/replayed event protection
- text/plain extraction
- application/pdf extraction
- deterministic chunking
- tenant-isolated document chunks with PostgreSQL RLS
- SQS delivery and execution failure handling
- custom application EventBridge bus
- full Phase 3 validation automation

Phase 3 final validation:

- Ruff: passed
- mypy: passed
- backend tests: 247 passed
- infrastructure tests: 21 passed
- git diff --check: passed

Final Phase 3 commit:

84e9376 Complete Phase 3 ingestion concurrency hardening

## Current next task

Begin Phase 4 architecture review before implementation.

The first Phase 4 slice should validate the planned retrieval architecture and
pgvector compatibility with the Aurora Data API before implementing embeddings
or retrieval.

## Phase 4 architecture questions to resolve first

- confirm the embedding model and configured vector dimensions
- inspect existing VectorStore port and adapters
- validate pgvector operations through the Aurora Data API
- determine parameter casting required for vector inserts and similarity queries
- preserve tenant isolation in retrieval
- define chunk embedding lifecycle and idempotency
- define retrieval result contract
- keep AWS SDK calls behind adapters
- do not put Bedrock SDK calls directly in services
- do not introduce VPC/NAT unless ADR-0008 is explicitly revisited

## Before editing

Read:

- AGENTS.md
- docs/architecture/00-phase0-architecture.md
- docs/adr/0008-lambda-outside-vpc-via-aurora-data-api.md
- docs/development/phase3-agent-playbook.md
- backend/app/ports/vector_store.py
- backend/app/adapters/
- backend/app/core/config.py
- backend/app/core/container.py
- backend/app/models/
- backend/migrations/
- backend/pyproject.toml

## Stop conditions

Stop and request review if:

- pgvector cannot be used safely through Aurora Data API
- vector dimensionality is unclear
- the proposed implementation requires Lambda VPC attachment
- a NAT Gateway appears necessary
- tenant isolation would need weakening
- existing repository/service boundaries would need to be bypassed
- embedding model configuration conflicts with architecture docs

## Validation

Full project validation:

powershell -ExecutionPolicy Bypass -File .\scripts\validate-all.ps1

## Commit policy

Do not commit or push until:

- focused validation passes
- full validation passes
- git diff --check passes
- architecture review is complete
