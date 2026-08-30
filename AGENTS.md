# CloudGuard AI Agent Instructions

This repository is developed incrementally with strict architectural and validation constraints.

## Source of truth

The current Git branch and repository contents are the source of truth.

Do not merge or copy older Phase 3 implementations over the current branch without reviewing differences first.

Do not silently replace accepted architectural decisions.

## Current phase

Current implementation phase: Phase 4 - Retrieval-Augmented Generation (Complete).
Next implementation phase: Phase 5 - Deterministic Compliance & Risk Scoring.

Phase 4 completed vector persistence, embedding generation lifecycle, tenant-isolated retrieval orchestration, and RAG answer generation.

Phase 5 must not begin until the Phase 4 validation checkpoint is green and reviewed.

## Architecture constraints

Follow the accepted ADRs and Phase 0 architecture.

Important Phase 3 constraints:

- Presigned document uploads use S3.
- Event-driven ingestion is orchestrated through EventBridge and Step Functions.
- Failed ingestion executions must have a DLQ path.
- Deployed Lambda functions remain outside the database VPC.
- Deployed Lambda database access uses Aurora Serverless v2 Data API.
- Do not deploy the asyncpg-backed tenant_session() inside an out-of-VPC Lambda.
- Local development and CI continue to use PostgreSQL through SQLAlchemy and asyncpg.
- Never provision a NAT Gateway for this architecture.
- Preserve PostgreSQL Row-Level Security tenant isolation.
- Tenant-scoped database operations must establish app.current_organization_id.
- AWS SDK calls belong only inside app/adapters/.
- Application services depend on ports, not boto3.
- Do not bypass the document-processing persistence port.

## Document-processing persistence

DocumentProcessingService must depend on DocumentProcessingRepository.

Local and CI use:

app/adapters/local/document_processing_repository.py

Deployed AWS ingestion uses:

app/adapters/aws/document_processing_repository.py

The AWS adapter must preserve the same tenant isolation behavior as the SQLAlchemy implementation.

## Security rules

- Never weaken or remove RLS to make tests pass.
- Never use a BYPASSRLS role for application execution.
- Never expose credentials or commit .env.
- Never hard-code AWS credentials.
- Do not trust organization_id supplied by unscoped database queries.
- Keep tenant filtering in application SQL even when RLS is active.
- Do not log secrets, tokens, database passwords, or document contents.

## Coding rules

- Python code must pass Ruff.
- Python code must pass mypy.
- Keep AWS SDK imports inside app/adapters/.
- Prefer explicit domain models at ports instead of ORM models.
- Do not introduce SQLAlchemy types into app/ports/.
- Preserve existing error semantics unless the task explicitly changes them.
- Keep changes focused on the current task.
- Avoid unrelated refactors.

## PowerShell environment

Development runs on Windows PowerShell.

Use:

python -m pytest
python -m ruff
python -m mypy
python -m alembic

Do not use Unix environment assignment syntax such as:

DB_USER=value command

Use PowerShell environment variables instead.

For DB integration tests:

$env:DB_USER="cloudguard_app"
$env:DB_PASSWORD="cloudguard_app"
$env:RUN_DB_TESTS="1"

The cloudguard_app role is intentionally NOBYPASSRLS.

## Validation workflow

After a small Python code change:

python -m ruff check <changed files>
python -m mypy <changed files>

After behavioral changes:

run focused pytest tests for the affected feature.

Before any Phase 3 commit:

python -m ruff check .
python -m mypy app

$env:DB_USER="cloudguard_app"
$env:DB_PASSWORD="cloudguard_app"
$env:RUN_DB_TESTS="1"

python -m pytest -ra
git diff --check

Do not commit if any required validation fails.

## Git rules

- Work on the current feature branch.
- Do not switch to main unless explicitly instructed.
- Do not force push.
- Do not amend existing pushed commits unless explicitly instructed.
- Inspect git status before staging.
- Stage only intended files.
- Run git diff --cached --check before committing.
- Do not commit generated secrets, .env files, caches, build artifacts, or temporary extraction directories.
- Do not push if tests are failing.

## Phase 4 completed work

1. pgvector schema, migrations, and HNSW cosine index.
2. Local SQLAlchemyVectorStore and deployed AuroraDataAPIVectorStore.
3. Decoupled EmbeddingProvider and LLMProvider boundaries.
4. Titan Embed Text v2 Bedrock embedding provider.
5. Ingestion worker embedding generation + vector persistence before READY.
6. Tenant-isolated, clearance-bounded RetrievalService.
7. Grounded, prompt-injection resistant RAGService with deterministic citations.
8. Bedrock Converse LLM generation adapter with fail-closed validation.
9. Retrieval and RAG API endpoints guarded by DOCUMENT_READ.
10. Comprehensive unit and PostgreSQL integration tests with RLS.

## Phase 5 roadmap (Deterministic Compliance & Risk Scoring)

1. Compliance evaluation and risk estimation domain models.
2. Versioned deterministic Python risk scoring engine.
3. Bounded LLM structured extraction for component estimates.
4. Audit trail and domain events for evaluations.

## Working style for coding agents

Before editing:

1. Inspect the relevant files.
2. Check git status.
3. Identify the smallest change required.

While editing:

1. Make one coherent slice.
2. Run focused validation.
3. Fix failures before continuing.

Before finishing:

1. Run full validation appropriate to the slice.
2. Show git diff --stat.
3. Show remaining known risks or TODOs.
4. Do not claim Phase 3 is complete unless the Phase 3 acceptance criteria are actually satisfied.

If architecture and implementation disagree, stop and surface the mismatch rather than silently choosing a new architecture.
