# CloudGuard AI Agent Instructions

This repository is developed incrementally with strict architectural and validation constraints.

## Source of truth

The current Git branch and repository contents are the source of truth.

Do not merge or copy older Phase 3 implementations over the current branch without reviewing differences first.

Do not silently replace accepted architectural decisions.

## Current phase

Current implementation phase: Phase 3 - Document Ingestion.

The Phase 3 goal is a reliable document ingestion pipeline that produces tenant-isolated document chunks and reaches READY for supported documents.

Phase 4 must not begin until the Phase 3 validation checkpoint is green.

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

## Phase 3 remaining work

Continue in this general order unless repository state shows a task is already complete:

1. Complete Aurora Data API document-processing repository.
2. Add focused Data API adapter tests.
3. Wire deployed worker persistence to the Data API boundary.
4. Reconcile the production ingestion trigger with the accepted S3 -> EventBridge -> Step Functions architecture.
5. Add Phase 3 CDK infrastructure.
6. Add PDF extraction support.
7. Ensure ingestion failures reach the DLQ.
8. Validate document status transitions.
9. Run full backend and infrastructure validation.
10. Commit and push a clean Phase 3 checkpoint.

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
