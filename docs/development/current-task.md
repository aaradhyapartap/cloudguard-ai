# Current Task

Phase 12 — Polish & Handoff (`phase-12-polish`)

- **Active Branch:** `phase-12-polish`
- **Base Commit (`phase-11-devops` HEAD):** `a318c0c45c70db857dc727061c66ad701b164d5e`
- **Active Handoff:** [`docs/development/phase12-handoff.md`](phase12-handoff.md)
- **Published Stacked PR Chain (Committed & CI-Verified on `origin`):**
  1. **PR #1 (`phase-8-security` → `main`):** `00f38ece21e9418eace66a7fb63b449a9f35dd2d`
  2. **PR #2 (`phase-9-evaluation` → `phase-8-security`):** `deb19cfe9edbcbf7100313000ca4d20425444e6c`
  3. **PR #3 (`phase-10-observability` → `phase-9-evaluation`):** `4d7aa9c5b0f68d1858d757484e4df2f7057120e1`
  4. **PR #4 (`phase-11-devops` → `phase-10-observability`):** `a318c0c45c70db857dc727061c66ad701b164d5e` (CI Run `42349837636`: all 4 jobs `success`)

> **Historical Section Note:** The Phase 8–11 sections below retain the chronological slice notes from their respective development phases. Phases 8–11 are now committed and verified in GitHub Actions CI on their stacked branches above; only the Phase 12 documentation changes on `phase-12-polish` remain unstaged in the working tree for review.

---

## Historical Phase 8 - Security Hardening (Completed on `phase-8-security`, PR #1)

## Objective

Harden the completed Phase 1-7 platform against application, tenant, AI, data
leakage, abuse, and operational threats without weakening the existing authority
boundaries.

Phase 8 begins from published Phase 7 commit
`fb9523064cf9b1b7b22d9427026d2b279dda67d7` on branch `phase-8-security`.

## Working security invariants

- The authenticated human `Principal` remains authoritative for organization,
  actor, role, and clearance.
- Models and retrieved documents are untrusted data, never an authority source.
- Tenant checks remain present in application SQL and PostgreSQL forced RLS.
- The application database role remains `NOBYPASSRLS`.
- Deterministic Python scoring remains authoritative.
- Only Manager may decide an approval; Analyst, Admin, model, and workflow may not.
- Task tokens remain internal and absent from HTTP, public models, audit payloads,
  and logs.
- The approved-action worker reloads persisted human state and accepts no action
  authority from Step Functions input.
- Published migrations are never edited. Corrections use a forward migration.
- Security failures are sanitized and fail closed.

## Phase 8.1 - Threat model and security baseline

Deliver:

- `SECURITY.md`;
- `docs/THREAT_MODEL.md` with assets, actors, boundaries, flows, threats, controls,
  and honest residual risk;
- `docs/security/AUTHORIZATION_MATRIX.md`;
- an ADR establishing PostgreSQL as the canonical approval audit ledger;
- exact CORS validation and production HTTPS-origin enforcement;
- removal of the retired `x-dev-principal` CORS allowance;
- bounded validation of reflected client request IDs; and
- RAG enforcement of both `DOCUMENT_READ` and `AI_QUERY`.
- forward migration `0012` normalizing exact least-privilege runtime access across
  all current application tables after clean database rebuilds.

Done when documentation distinguishes implemented controls from Phase 0 target
architecture, focused tests pass, the authorization route matrix is green, Ruff and
mypy pass, and no Phase 7 invariant changes.

The migration regression was discovered by running the authorization route matrix
through `cloudguard_app` after a clean rebuild. The runtime may read organizations
and compliance definitions but cannot provision or mutate them. Mutable application
tables receive only the select/insert/update subset their repositories exercise;
append-only tables retain select/insert or read-only grants. Forced RLS remains the
tenant boundary, and the role receives no delete, truncate, reference, trigger, or
audit-sequence privilege. CI creates the role before migrations and does not issue
blanket table or sequence grants after migrations complete. Privileged fixture setup
uses a test-only admin connection rather than widening production permissions.

## Phase 8.2 - Prompt injection and model guardrails

Deliver:

- one shared provider-neutral untrusted-context framing policy;
- per-request unpredictable context delimiters or an equivalently robust isolation
  format across RAG, Compliance, Risk, and Reviewer prompts;
- explicit separation of system policy, user request, trusted identifiers, and
  untrusted retrieved content;
- Bedrock Guardrail configuration validation and fail-closed intervention handling;
- model output checks that reject invented evidence/control/tool identifiers;
- a reusable adversarial prompt-injection corpus for Phase 9; and
- tests proving injected text cannot escalate role, tenant, clearance, tools,
  approval, or write authority.

Carry forward the missing regression proving `control_ids=None` cannot accept a
model-invented UUID. Reject `control_ids=[]` at the contract boundary instead of
interpreting it as an ambiguous scope.

Implementation and local validation completed on 2026-10-31; commit pending.

- Shared nonce framing and response-safety checks cover all seven generation paths.
- Production Guardrail configuration and fail-closed intervention handling are implemented.
- Unknown identifier/tool checks and the reusable adversarial corpus are covered.
- Omitted control scopes reject invented controls; explicit empty scopes are rejected.
- Full backend suite: 912 passed, 92% coverage, including PostgreSQL integration tests.
- Ruff: clean. Mypy: clean across 131 application source files.
- PostgreSQL revision: 0012. Published migrations unchanged.
- Exactly 33 expected paths remain unstaged; diff whitespace checks passed.
- Local validation used Python 3.14; the configured Python 3.12 CI runtime remains
  a separate verification.
- Bedrock tests use controlled provider responses, not live AWS Guardrail evaluation.
  Deployment must provision and configure the Guardrail resource separately.

## Phase 8.3 - PII and sensitive-data controls

Slice 3 local validation passed on 2026-10-31; commit pending.

- Full backend suite: 1006 passed, 92% coverage, including PostgreSQL integration tests.
- Infrastructure suite: 38 passed.
- Backend Ruff: clean. Backend mypy: clean across 134 source files.
- Infrastructure mypy: clean across 4 source files.
- PostgreSQL revision: 0012. Published migrations unchanged.
- Validation used local Python 3.14; Python 3.12 CI remains separately unverified.
- No deployment, live-provider evaluation, backfill, or reindex was performed.

Implemented: allowlisted retrieval/RAG metadata, sanitized intentional error
projections, standard-library logging privacy controls, worker failure
serialization, and explicit CloudWatch workflow-payload exclusion.
See `docs/security/PROJECTIONS_AND_RUNTIME_PRIVACY.md`. Original authorized
evidence and native workflow history remain intentionally retained sensitive
surfaces. Deployment IAM/runtime verification remains required.

Slice 2 local validation passed on 2026-10-31; commit pending.
Validation: 986 backend tests passed, 92% coverage, Ruff clean, and mypy clean
across 132 source files. PostgreSQL integration tests passed; migrations unchanged.
Validation used local Python 3.14; Python 3.12 CI remains separately unverified.
No live-provider privacy evaluation or reindex was performed.

Implemented: provider-neutral bounded sensitive-data
detection, minimized generation/embedding inputs, rejected sensitive model output,
and persisted category/policy annotations on newly ingested original chunks. See
`docs/security/DATA_HANDLING.md` for coverage and limitations. Remaining Phase 8.3
work includes metadata/evidence projection, intentional error responses, platform
logging, and workflow-history review. No backfill or reindex is performed.

Slice 1 local validation passed on 2026-10-31; commit pending.
Validation: 939 backend tests passed, 92% coverage, Ruff clean, and mypy clean
across 131 source files. PostgreSQL integration tests passed; migrations unchanged.
Validation used local Python 3.14; Python 3.12 CI remains separately unverified.

Implemented: safe application logging and sanitized
request-validation errors. See `docs/security/SAFE_LOGGING.md` for the implemented
boundary and remaining logging/platform risks. This is not completion of Phase 8.3;
PII detection, model input/output protection, and evidence-projection review remain.

Deliver:

- explicit data classification and PII handling policy;
- provider-neutral detection/redaction contracts with local deterministic tests;
- context minimization before model invocation;
- output handling that does not leak prohibited sensitive values;
- logging minimization and removal of raw exception strings, storage keys,
  filenames, prompts, and document content;
- recursive redaction coverage for tuples and other structured values;
- secret, credential, task-token, presigned-URL, and PII leakage tests; and
- document confidentiality enforcement review across retrieval and evidence
  projections.

Do not silently discard compliance evidence. Detection, tagging, quarantine, or
redaction decisions must remain auditable.

## Phase 8.4 - Rate limiting and abuse protection

Slice 4 local validation passed on 2026-11-01; commit pending.

- Full backend suite (including Phase 8.5 security regressions): 1223 passed, 92% coverage, including PostgreSQL integration and RLS tests.
- Infrastructure suite: 47 passed.
- Backend Ruff: clean. Backend mypy: clean across 147 source files (`app` + `tests/security`).
- Infrastructure mypy: clean across 5 source files.
- PostgreSQL revision: 0012. Published migrations unchanged.
- Local `AgentWorkflow` requires `WorkAdmission` (`WorkKind.MODEL`) and enforces both `Permission.DOCUMENT_READ` and `Permission.AI_QUERY` before any model work (admitting one local workflow run rather than each deployed worker step).
- Validation used local Python 3.14; Python 3.12 CI remains separately unverified.
- No deployment, live AWS enforcement evaluation, or external IAM mutation was performed.

Slice 4 summary:

Shared per-tenant worker admission gates Research, Risk, Reviewer, and ingestion
before costly work. Fixed-window quotas and owner-checked expiring slots use
separate namespaces in the existing operational admission table. CDK supplies
the table and narrowly scoped permissions to those four workers only. Local
development uses bounded in-memory stores; non-local workers fail closed without
the shared table. No migration, deployment, or external IAM mutation is performed.
See `docs/security/WORKER_ADMISSION.md` for failure and rollout semantics.

This bounds active worker steps, not queued workflow starts, pending approvals,
API model concurrency, or provider-side work after a timeout. Workflow-start
admission, edge/API wiring, native input-token budgets, parser isolation, and
live enforcement verification remain outstanding. Human approval and the
ingestion DLQ are unchanged. Python 3.12 CI remains separately unverified.

Slice 3 local validation passed on 2026-11-01; commit pending.

- Full backend suite: 1090 passed, 92% coverage, including PostgreSQL integration tests.
- Infrastructure suite: 44 passed.
- Backend Ruff: clean. Backend mypy: clean across 140 source files.
- Infrastructure mypy: clean across 5 source files.
- PostgreSQL revision: 0012. Published migrations unchanged.
- Large-payload parametrizations use short test IDs for Windows compatibility.
- Validation used local Python 3.14; Python 3.12 CI remains separately unverified.
- No deployment, live AWS enforcement evaluation, migration, or model replacement was performed.

Implemented: shared generation-envelope/output budgets,
positive bounded output-token requests, configured Bedrock output caps, embedding
batch/input/response bounds, explicit single-attempt Bedrock SDK calls, bounded
Lambda admission-throttle retries, ingestion delivery retries, and reserved
concurrency for deployed model/ingestion workers. See `docs/security/RESOURCE_BUDGETS.md`.
Human approval wait, authorization, original evidence, and ingestion DLQ are retained.
Native input-token counting is not claimed: the configured Nova Pro reviewer does
not support CountTokens. Workflow-start admission, tenant concurrency, edge/API
deployment wiring, and live resource-limit verification remain outstanding.
No deployment, migration, or model replacement is performed by this slice.

Slice 2 local validation passed on 2026-10-31; commit pending.

- Full backend suite: 1066 passed, 92% coverage, including PostgreSQL integration tests.
- Infrastructure suite: 40 passed.
- Backend Ruff: clean. Backend mypy: clean across 139 source files.
- Infrastructure mypy: clean across 5 source files.
- PostgreSQL revision: 0012. Published migrations unchanged.
- Validation used local Python 3.14; Python 3.12 CI remains separately unverified.
- No deployment, live AWS enforcement evaluation, or external IAM change was performed.

Implemented: centralized request admission for existing
API routes, per-peer authentication/login quotas, verified user/tenant budgets,
bounded local counters, and a conditional DynamoDB counter adapter/stack.
Deployed API composition requires RATE_LIMIT_TABLE_NAME and never falls back to
process memory. API role/environment wiring, trusted proxy configuration, and
live shared-counter verification remain deployment requirements. No workflow-start
HTTP route exists to attach a gate to; workflow/ingestion admission, concurrency,
model/retry bounds, and edge/WAF controls remain outstanding. See
`docs/security/RATE_LIMITING.md`. No deployment or migration change is performed.

Slice 1 local validation passed on 2026-10-31; commit pending.

- Full backend suite: 1033 passed, 92% coverage, including PostgreSQL integration tests.
- Backend Ruff: clean. Mypy: clean across 135 source files.
- PostgreSQL revision: 0012. Published migrations unchanged.
- Validation used local Python 3.14; Python 3.12 CI remains separately unverified.
- No deployment, live AWS validation, backfill, reindex, or deletion was performed.

Implemented: centralized per-document ingestion budgets
bound registered/actual bytes, S3 worker reads, PDF pages, extracted characters,
and chunks before persistence or embedding. See `docs/security/INGESTION_BUDGETS.md`.
Rate limiting, concurrent work admission, model/retry budgets, parser resource
isolation, and deployed edge/storage enforcement remain outstanding. No deployment
or published migration change is performed by this slice.

Deliver one centralized policy with deterministic tests for:

- authentication and local login;
- retrieval and RAG;
- compliance/model extraction;
- agent workflow start;
- approval read/decision APIs; and
- document registration/completion and ingestion.

Also bound registered and actual upload size, PDF pages/extracted characters,
chunks, model tokens, retry counts, and concurrent cost-bearing work. Application
limits and edge/API Gateway/WAF limits are defense-in-depth, not substitutes.

## Phase 8.5 - Security regression and CI

Local validation passed on 2026-11-01; commit pending.

- Dedicated `backend/tests/security/` suite (`test_security_regressions.py`: 94 deterministic service/route/contract tests, `test_callback_race.py`: 6 mocked RDS Data API / callback-port race tests, `test_rls_security.py`: 7 real PostgreSQL `cloudguard_app` `NOBYPASSRLS` tests + 1 migration-file sequence check gated by `REQUIRE_SECURITY_DB_TESTS=1`): 108 passed.
- `.github/workflows/ci.yml` updated with blocking `Security regressions` step (`REQUIRE_SECURITY_DB_TESTS=1`), dedicated `infrastructure` job (`mypy stacks` + `pytest -ra`), blocking `pip-audit` for `backend` and `infrastructure` (verified 0 vulnerabilities), blocking `npm audit --audit-level=high` (verified 0 high/critical vulnerabilities; 14 moderate dev-only `eslint`/`minimatch` advisories remain), executable frontend component tests (`npm test`), `gitleaks`, and `aquasecurity/trivy-action@57a97c7e7821a5776cebc9bb87c984fa69cba8f1` (`v0.35.0`) container image scanning without `ignore-unfixed: true` (using a component-scoped, time-bounded `.trivyignore.yaml` targeting `usr/local/bin/aws-lambda-rie`).
- Special review items resolved in source:
  - Callback synchronization race (`CG-14`): `AuroraDataAPIApprovalExecutionRepository.execute_for_workflow` uses bounded readiness polling (`CallbackDeliveryNotCommittedError`, 4 attempts, 50 ms backoff, rolling back the `FOR UPDATE` row lock between attempts and failing closed with a sanitized `UpstreamError` if rollback fails) and fails closed without side-effect execution when `callback_delivered_at` remains uncommitted.
  - Historical migration `0005`: audited against `tables.py` and live PostgreSQL `pg_indexes` at revision `0012`; all 17 expected indexes/unique constraints are present and enforced by `test_migration_0005_expected_indexes_exist_in_postgresql`. Published migrations `0001`–`0012` are unchanged.
  - Hosted login PKCE/state/nonce and lifecycle safety: `frontend/src/lib/auth.ts`, `AuthProvider.tsx`, `AuthProvider.test.tsx`, and `login/page.tsx` implement RFC 7636 PKCE (`S256`, 48-byte verifier), 24-byte `state` and `nonce`, single-use `sessionStorage` transaction records (`cloudguard.auth.pkce`), `/oauth2/token` code exchange, ID token claim verification (`token_use=id`, `iss`, `aud`, `nonce`, `exp`), callback URL scrubbing, and cancellation/logout lifecycle safety (`npm test` 6 passed, `npm run lint`, `npm run typecheck`, and `npm run build` pass). Static SPA `sessionStorage` XSS exposure and live Cognito Hosted UI runtime verification remain documented residual/deployment items.

Deliver:

- dedicated `tests/security/` suites;
- cross-tenant, clearance, RBAC, forged identity/organization, prompt injection,
  tool misuse, invented evidence, approval bypass, token leakage, side-effect
  bypass, rate-limit, PII, and safe-logging cases;
- blocking security CI jobs;
- effective Python and npm dependency audits;
- gitleaks and container scanning; and
- a final security/architecture review.

The injection/adversarial corpus and authorization matrix are blocking. No live
model call is required for deterministic CI security tests.

## Phase 8 special review items

### Callback synchronization race

`SendTaskSuccess` can resume Step Functions before `callback_delivered_at` is
committed. The execution repository currently fails closed. Add a bounded readiness
retry/barrier or safe reconciliation keyed to a distinct error condition. Never mark
the callback delivered before provider success and never claim distributed exactly
once.

### Approval audit storage

PostgreSQL remains canonical for approval audit events. Do not add a direct DynamoDB
dual write. A future projection requires a durable outbox/replay design. No general
`AuditEvent` or `AgentExecution` subsystem currently exists.

### Historical migration 0005

Audit the expected indexes against a real upgraded database. If compatibility work
is necessary, create a new idempotent forward migration; do not rewrite `0005`.

### Hosted login and browser token storage

Complete PKCE, state, nonce, and callback handling before describing Cognito hosted
login as production-ready. The static SPA's `sessionStorage` token remains a
documented XSS/session-theft residual risk.

## Explicit non-goals

Phase 8 does not introduce:

- arbitrary or autonomous remediation;
- model-decided approvals;
- emergent agent delegation;
- identity or tenant authority from model/workflow/request payloads;
- a distributed exactly-once callback claim;
- a fictional DynamoDB audit subsystem; or
- a weakening of RLS, RBAC, evidence provenance, or approval constraints.

## Validation and publishing

For each coherent subphase:

1. Run focused tests.
2. Run relevant backend and infrastructure regressions.
3. Run Ruff and mypy for affected Python.
4. Run real PostgreSQL tests for persistence/RLS changes.
5. Run `git diff --check` and security/architecture review.
6. Confirm the exact changed-file inventory.
7. Stage only that inventory after all checks pass.
8. Verify published migrations are unchanged.
9. Commit, push, fetch, and compare the full local/remote hashes.

Do not publish a subphase while any required control or regression is failing.

---

## Phase 10 - Observability (Autonomous Implementation & Review Corrections)

**Branch:** `phase-10-observability`
**Base commit:** `deb19cfe9edbcbf7100313000ca4d20425444e6c`
**Handoff:** `docs/development/phase10-handoff.md`

### Scope & Status:
- Implemented provider-neutral telemetry contracts in `app/ports/telemetry.py`.
- Total boundary failure isolation via `safe_telemetry_call` in `app/telemetry/isolation.py`.
- Strict bounded metric dimension validation, closed value sets, and canary pattern scrubbing in `app/models/telemetry.py`.
- Implemented explicitly versioned Bedrock model pricing catalog (`2026-03-01`), region (`us-east-1`), tier (`On-Demand`), and source URL in `app/telemetry/pricing.py`.
- Implemented local in-memory emitter with bounded retention (`deque(maxlen=self._max_records)`) and test doubles.
- Implemented AWS CloudWatch Embedded Metric Format (EMF) adapter (`CloudWatchEMFTelemetryEmitter`) with explicit multi-dimension sets and stdout documentation.
- Integrated operational telemetry into `RetrievalService`, `RAGService`, `AgentWorkflow`, `WorkAdmission`, `RequestContextMiddleware`, and FastAPI dependency layer (`deps.py`).
- Wired deployed worker telemetry in `deployed_research_agent_worker`, `deployed_risk_agent_worker`, `deployed_reviewer_agent_worker`, `deployed_document_worker`, and local `document_worker`.
- Added model usage and latency recording immediately upon provider generation across Research, Risk, Reviewer, and Ingestion without double-counting.
- Implemented CloudWatch operational dashboard and 7 CloudWatch alarms in CDK `ObservabilityStack`:
  - DynamoDB `SystemErrors` monitored across actual operations (`UpdateItem`, `PutItem`, `DeleteItem`) via Metric Math `m0 + m1 + m2`.
  - Honest estimated-cost-per-query view using supported CloudWatch Metric Math conditional `IF(measured > 0, cost / measured)` with `CostMeasuredQueries` denominator, cleanly omitting unmeasured periods without fabricating zero cost.
  - Separated model-call accounting (`CostMeasuredModelCalls`, `ModelCallEstimatedCostUSD`) from query boundary cost accounting (`CostMeasuredQueries`, `EstimatedInferenceCostUSD`, `CostUnmeasuredQueries`, `CostPartialQueries`).
  - Alarms configured with `TreatMissingData.NOT_BREACHING`.
  - Deduplicated EMF dimension sets by unordered identity (`frozenset`).
  - Closed-set precedence before broad canary checks and explicit allowlists for operational `safe_attributes`.
  - Extracted available token usage and latency at the Bedrock response boundary before guardrail intervention or output validation.
  - Wired shared telemetry into `_build_llm`, `_build_reviewer_llm`, and all container builders (`build_container`, `build_research_agent_worker_container`, `build_risk_agent_worker_container`, `build_reviewer_agent_worker_container`).
  - Preserved unknown usage provenance on successful responses without inventing `TokenUsage(0, 0)`; distinguished known zero usage from absent/incomplete usage; ensured affected query costs remain partial/unmeasured.
  - Removed unsupported numeric overhead estimate from EMF adapter docstring.
- Complete test suites passed:
  - Backend full suite: 1,327 passed, 0 failed, 0 skipped in 37.63s (real PostgreSQL + pgvector).
  - Telemetry unit tests: 48 passed, 0 failed, 0 skipped in 0.92s.
  - Focused unit suites: 85 passed in 1.64s (`test_container.py`, `test_bedrock_chat_llm.py`, `test_telemetry.py`).
  - CDK infrastructure suite: 52 passed, 0 failed, 0 skipped in 217.87s.
  - Evaluation CLI: Security Gate PASS, Advisory Quality WARN (50 golden + 16 adversarial cases).
  - Frontend vitest: 8 passed.
  - Ruff and mypy: clean across backend (165 files) and infrastructure (15 files).
- Committed on `phase-10-observability` (`126d3b06e9521442534ac245397453e9eadcd536` + `.gitleaksignore` follow-up `4d7aa9c5b0f68d1858d757484e4df2f7057120e1`, Draft PR #3) and verified in GitHub Actions CI.

---

## Phase 11 - DevOps (Completed on `phase-11-devops`, PR #4)

**Branch:** `phase-11-devops`
**Base commit:** `4d7aa9c5b0f68d1858d757484e4df2f7057120e1`
**Published commits:** `f267aa028df8688dbaac452be4dd0ff3aba928f9` + `a318c0c45c70db857dc727061c66ad701b164d5e` (`fix(ci): declare PyYAML for infrastructure workflow tests`)
**Handoff:** `docs/development/phase11-handoff.md`

### Scope & Status:
- Created least-privilege GitHub Actions OIDC stack (`GitHubOIDCStack`) enabling keyless deployment via `sts:AssumeRoleWithWebIdentity` strictly scoped to `repo:aaradhyapartap/cloudguard-ai:environment:{environment_name}` and audience `sts.amazonaws.com`.
- Bound CDK bootstrap role access to explicit qualifier `hnb659fds` without wildcard `*`.
- Denied data-plane vector actions (`PutVectors`, `QueryVectors`, `DeleteVectors`) to the deployment role; scoped resource-capable read actions to `cloudguard-*`.
- Added protected manual deployment workflow (`.github/workflows/deploy.yml`) with `workflow_dispatch`, verified CI requirement on exact commit, explicit stack allowlist validation, safely quoted argument arrays, and required confirmation tokens for production.
- Implemented Amazon S3 Vectors adapter (`backend/app/adapters/aws/s3_vector_store.py`) implementing the `VectorStore` port contract with strict tenant isolation, confidentiality clearance verification, deterministic key formatting, contradictory ID suppression, nonfinite distance rejection, and foreign-tenant deletion refusal.
- Implemented opt-in vector store benchmark CLI (`scripts/benchmark_vector_stores.py`) supporting zero-cost offline synthetic fixtures (with true cosine similarity), local PostgreSQL `local_pgvector` mode, and gated live-cloud execution.
- Added infrastructure CDK unit tests (`infrastructure/tests/test_oidc_stack.py`) asserting OIDC provider, trust policies, least-privilege statements, negative trust regressions, and S3 Vectors grants.
- Explicitly inventoried the out-of-VPC Lambda / Data API persistence boundary for the FastAPI HTTP application.
- Declared `PyYAML>=6.0.2,<7` in `infrastructure/requirements.txt` for `.github/workflows/deploy.yml` CDK workflow tests.
- Complete test suites passed locally and in GitHub Actions CI (Run `42349837636`, all 4 jobs `Backend`, `Infrastructure`, `Frontend`, `Security` succeeded):
  - Backend full suite: 1,392 passed, 0 failed, 1 warning in 49.60s (real PostgreSQL + pgvector under `cloudguard_app` with forced RLS).
  - Focused Phase 11 unit suites: 75 passed in 4.18s (`test_verify_deployment_ci.py`: 33, `test_s3_vector_store.py`: 20, `test_container.py`: 11, `test_benchmark_vector_stores.py`: 11).
  - Infrastructure pytest suite: 63 passed, 0 failed (including OIDC, deploy workflow scoping, negative trust, scoped IAM, S3 Vectors, and Bedrock Guardrail tests).
  - Offline CDK synthesis: successfully synthesized all 6 stacks.
  - Benchmark CLI: zero-cost synthetic fixture passed (100% recall); `local_pgvector` mode passed with unique IDs, collision checks, owned committed tracking, and scoped cleanup; live-cloud gate verified.
  - Ruff and mypy: clean across backend (168 files), infrastructure (18 files), and scripts.
- Published migrations `0001`–`0013` remain completely untouched.

---

## Phase 12 - Polish & Handoff (Active on `phase-12-polish`)

- **Branch:** `phase-12-polish`
- **Base commit:** `a318c0c45c70db857dc727061c66ad701b164d5e` (`phase-11-devops` HEAD)
- **Handoff:** [`docs/development/phase12-handoff.md`](phase12-handoff.md)

### Scope & Deliverables:
1. **Reconciled `README.md` & Setup Instructions:** Updated `README.md` from its Phase 8 snapshot to reflect the complete Phase 1–11 codebase (`17` domain ports, `6` CDK stacks, `1,392` backend tests, `63` CDK tests, `8` frontend Vitest tests, `108` security regressions, Alembic migrations `0001`–`0013`, and Phase 0–12 roadmap completion).
2. **Reproducible Commands Documented:** Documented end-to-end local startup (`scripts/local_setup.sh`, `Makefile`, and Windows PowerShell equivalents), PostgreSQL `cloudguard_app` (`NOBYPASSRLS`) role provisioning, Alembic migration (`0001`–`0013`) and synthetic seeding (`python scripts/seed_data.py`), backend/frontend startup, unit/DB/security/CDK test execution, fixture evaluation CLI (`scripts/run_evals.py`), and vector benchmark CLI (`scripts/benchmark_vector_stores.py`).
3. **Updated Architecture Diagrams:** Updated the Mermaid architecture diagrams in `README.md` and `docs/architecture/00-phase0-architecture.md` (§O.3) to explicitly distinguish:
   - **Implemented & Verified Local/CI Paths** (Next.js SPA, FastAPI over TCP `asyncpg`, PostgreSQL 16 + `pgvector` with forced RLS and canonical `approval_audit_events`, fixture AI providers, local workflows),
   - **Implemented CDK & Deployed-Worker Designs** (`CloudGuardIdentity`, `CloudGuardRateLimits`, `CloudGuardIngestion`, `CloudGuardAgentWorkflow`, `CloudGuardObservability`, `CloudGuardOIDC`, out-of-VPC worker Lambdas over RDS Data API, `BedrockLLMProvider`, `S3VectorStore`), and
   - **Unprovisioned Target / External Prerequisites** (CloudFront/WAF/API Gateway, FastAPI HTTP deployment target respecting the `asyncpg` TCP vs. Data API boundary, external Aurora Serverless v2 cluster, external Bedrock Guardrail resource, customer-managed KMS keys, and unimplemented general DynamoDB `AuditEvent`/`AgentExecution` subsystem).
4. **Synthetic Demo Walkthrough (`docs/DEMO_WALKTHROUGH.md`):** Created a concise, copy-pasteable walkthrough using only synthetic tenants (`Acme Manufacturing`, `Globex Industrial`) and synthetic accounts (`analyst@acme.test`, `manager@acme.test`, `admin@acme.test`, `analyst@globex.test`).
5. **ADR Consistency Review (`docs/adr/`):** Reconciled `docs/adr/README.md`, `ADR-0002`, `ADR-0008`, `ADR-0013`, and `ADR-0015` with Phase 8–11 implementations while preserving all accepted decisions.
6. **Evidence-Backed Interview Notes & Resume Bullets (`docs/INTERVIEW_NOTES.md`):** Authored tailored resume bullets and technical Q&A backed strictly by verifiable repository metrics and file citations without inventing production adoption, live model quality, cost savings, or live deployment.
7. **Deployment Prerequisites, Scanner Exceptions & Stacked PRs (`docs/development/phase12-handoff.md`):** Documented external AWS/GitHub prerequisites, `.trivyignore.yaml` expiry (`2026-12-01` for `CVE-2026-111943` on `usr/local/bin/aws-lambda-rie`), `.gitleaksignore` synthetic test fixture allowlists, and the sequential merge order for stacked PRs #1–#4.
8. **Working Tree Status:** All Phase 12 documentation updates remain unstaged on `phase-12-polish` for review; published migrations `0001`–`0013` and stacked PRs #1–#4 are untouched.
