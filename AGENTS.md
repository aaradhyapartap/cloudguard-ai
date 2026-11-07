# CloudGuard AI Agent Instructions

This repository is developed incrementally with strict architectural and validation constraints.

## Source of truth

The current Git branch and repository contents are the source of truth.

Do not merge or copy older phase implementations over the current branch without reviewing differences first.

Do not silently replace accepted architectural decisions.

## Current phase

Current implementation phase: Phase 12 - Polish & Handoff.

Phases 1-11 are implemented in code, committed, and verified in GitHub Actions CI
across the stacked branch chain through `a318c0c45c70db857dc727061c66ad701b164d5e`
on `phase-11-devops`. Branch `phase-12-polish` starts from that exact commit.

See `docs/development/current-task.md` and `docs/development/phase12-handoff.md`
for the active status, stacked PR chain, and deployment prerequisites.

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

## Completed checkpoint through Phase 11

1. Authenticated Principal, centralized RBAC, and tenant RLS (`FORCE ROW LEVEL SECURITY` under `NOBYPASSRLS` role `cloudguard_app`).
2. Document ingestion, `pgvector` and `S3VectorStore` retrieval, and deterministic citations.
3. Deterministic compliance/risk scoring with bounded model proposals.
4. Fixed Research -> Risk -> Reviewer workflow and fail-closed tool registry.
5. Genuine Manager approval pause, internal callback recovery, immutable PostgreSQL approval audit, and one bounded approved action (`create_security_review`).
6. Security hardening: threat model, per-request nonce prompt framing, Bedrock Guardrail fail-closed intervention handling, bounded PII minimization, central API rate admission, shared tenant worker-step admission (`WorkAdmission`), and 109 blocking security regression tests (`94` service/route/contract + `6` callback race + `9` PostgreSQL RLS/catalog).
7. Reproducible AI & security evaluation harness (`50` golden + `16` adversarial cases, `scripts/run_evals.py`) and `/analytics` UI.
8. Privacy-safe CloudWatch EMF observability (`CloudWatchEMFTelemetryEmitter`), separated model-call vs. user-query cost accounting, and `ObservabilityStack`.
9. Keyless GitHub OIDC deployment stack (`GitHubOIDCStack`), manual protected `.github/workflows/deploy.yml` with fail-closed CI provenance check (`scripts/verify_deployment_ci.py`), and vector store benchmark CLI (`scripts/benchmark_vector_stores.py`).

## Phase 12 priorities

1. Reconcile `README.md` and setup instructions against actual code.
2. Document reproducible local startup, migration/seed, test, evaluation, and benchmark commands.
3. Distinguish implemented local paths, deployed-worker designs, and unprovisioned infrastructure in architecture diagrams.
4. Provide a synthetic-only demo walkthrough (`docs/DEMO_WALKTHROUGH.md`) and evidence-backed interview notes (`docs/INTERVIEW_NOTES.md`).
5. Document deployment prerequisites, scanner-exception expiry, and stacked PR dependencies in `docs/development/phase12-handoff.md`.

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
