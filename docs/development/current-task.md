# Current Task

Phase 8 - Security Hardening

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
