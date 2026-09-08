# CloudGuard AI Agent Instructions

This repository is developed incrementally with strict architectural and validation constraints.

## Source of truth

The current Git branch and repository contents are the source of truth.

Do not merge or copy older phase implementations over the current branch without reviewing differences first.

Do not silently replace accepted architectural decisions.

## Current phase

Current implementation phase: Phase 8 - Security Hardening.

Phases 1-7 are complete. Phase 8 starts from published commit
`fb9523064cf9b1b7b22d9427026d2b279dda67d7` on `phase-8-security`.

Work one coherent Phase 8 subphase at a time. Do not reopen Phase 7 feature
development unless a real defect is found. See
`docs/development/current-task.md` for the active security plan and carry-forward
items.

## Architecture constraints

Follow the accepted ADRs and Phase 0 architecture.

Important deployed-architecture constraints:

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
- Treat uploaded/retrieved content and model output as untrusted data.
- Models never supply identity, tenant, clearance, approval, score, or action authority.
- Only Manager may decide approvals; Admin and Analyst may not.
- Keep Step Functions task tokens out of HTTP contracts, public models, audit payloads, and logs.
- Do not claim distributed exactly-once callback delivery.
- Do not add direct PostgreSQL/DynamoDB audit dual writes.
- Do not change published migrations; use a new forward migration.

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

Before any Phase 8 commit:

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

## Completed checkpoint through Phase 7

1. Authenticated Principal, centralized RBAC, and tenant RLS.
2. Document ingestion, pgvector retrieval, and deterministic citations.
3. Deterministic compliance/risk scoring with bounded model proposals.
4. Fixed Research -> Risk -> Reviewer workflow and fail-closed tool registry.
5. Genuine Manager approval pause, internal callback recovery, immutable approval
   audit, and one bounded approved action.

## Phase 8 priorities

1. Threat model and security baseline.
2. Prompt-injection defenses and Bedrock Guardrails.
3. PII, context, output, and logging protections.
4. Central rate limiting, ingestion bounds, and abuse protection.
5. Blocking security regression and CI gates.

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
4. Do not claim Phase 8 is complete until all five subphases and their acceptance
   criteria are satisfied.

If architecture and implementation disagree, stop and surface the mismatch rather than silently choosing a new architecture.
