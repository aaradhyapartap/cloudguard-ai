# Phase 8 Autonomous Security Hardening Handoff (Round 3 Review Reconciliation)

- **Repository:** `D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai`
- **Branch:** `phase-8-security`
- **Starting `HEAD`:** `78120521872e170d0138ec96da594c1ceb241f75`
- **Review Date:** `2026-11-01`
- **Working Tree Policy:** All changes remain **UNSTAGED** (`git diff --cached` is empty). No commits, tags, pushes, or cloud deployments were performed. Published migrations `0001`–`0012` are untouched.

---

## 1. Executive Summary

Phase 8 repository-level security hardening across Phase 8.4d, Phase 8.4 reconciliation, Phase 8.5 security regression and CI enforcement, all four Phase 8 special review items, and the Round 2 / Round 3 independent review findings has been implemented and verified:

1. **Phase 8.4d Tenant Worker Admission:** Shared per-tenant fixed-window rate limits (`InMemoryRateLimitStore`, `DynamoDBRateLimitStore`) and expiring DynamoDB concurrency slot leases (`WorkAdmission`, `WorkLeaseStore.try_acquire` / `WorkLeaseStore.release`, `InMemoryWorkLeaseStore`, `DynamoDBWorkLeaseStore`) gate the four cost-bearing worker boundaries (`Research`, `Risk`, `Reviewer` sharing 60 steps/min and 2 slots/tenant; `ingestion` with 10 steps/min and 1 slot/tenant). Key namespaces (`api:v1:*`, `worker:v1:*`, `slot:v1:*`) and `infrastructure/stacks/rate_limit_stack.py` IAM helpers (`RateLimitStack.grant_admission` for `api:v1:*`, and module-level `grant_worker_admission` for `worker:v1:*` and `slot:v1:*`) using `dynamodb:LeadingKeys` are strictly isolated. Non-local composition fails closed when `RATE_LIMIT_TABLE_NAME` is absent.
2. **Phase 8.4 Local Workflow Authority & Required Admission:** `AgentWorkflow` (`backend/app/services/agent_workflow.py`) requires `admission: WorkAdmission` as a mandatory constructor keyword argument (no `admission=None` bypass) and enforces both `require_permission(principal, Permission.DOCUMENT_READ)` and `require_permission(principal, Permission.AI_QUERY)` before entering `self._admission.admit(organization_id=principal.organization_id, kind=WorkKind.MODEL)` and invoking any agent/model work. This bounds one local workflow execution and is documented as distinct from deployed Step Functions orchestration, where each worker Lambda (`Research`, `Risk`, `Reviewer`) independently enforces step-level admission.
3. **Hosted-Login Lifecycle Race & Executable Frontend Suite:**
   - `frontend/src/components/auth/AuthProvider.tsx` captures callback parameters (`captureHostedCallback`) synchronously on effect setup without scrubbing `window.location` before `/auth/config` resolves, coordinates single-flight code exchange via `runSingleFlightCallbackExchange`, and tracks effect cancellation (`cancelled`) plus a monotonic session epoch (`sessionEpochRef` incremented on `endSession`/`logout`/`401` and `loginLocal`).
   - Executable Vitest + React Testing Library tests in `frontend/src/components/auth/AuthProvider.test.tsx` (**6 passed**) cover both `/auth/config` resolution orderings under `<StrictMode>`, unmount/cleanup during `/oauth2/token` exchange, failed token exchange, stale `/me` resolution after `logout()`, and `buildHostedAuthorizeUrl` PKCE (`S256`, 48-byte verifier) + 24-byte `state`/`nonce` persistence in `cloudguard.auth.pkce`. Wired `cd frontend && npm test` into `.github/workflows/ci.yml`.
4. **Callback Readiness Rollback Safety:**
   - `AuroraDataAPIApprovalExecutionRepository._acquire_ready_approval` (`backend/app/adapters/aws/approval_execution_repository.py`) does not catch and ignore `UpstreamError` on `await self._rollback(transaction_id)` inside `except CallbackDeliveryNotCommittedError:`. If rollback fails, `_acquire_ready_approval` immediately fails closed with the sanitized `UpstreamError` without sleeping, starting a second transaction/read, or executing any side effect.
5. **Component-Scoped Container Scanning Exception (Round 3 Item 1):**
   - Pinned `aquasecurity/trivy-action@57a97c7e7821a5776cebc9bb87c984fa69cba8f1` (`v0.35.0`, which defaults to Trivy `v0.69.3`) in `.github/workflows/ci.yml`, removed blanket `ignore-unfixed: true`, removed the unscoped plain-text `.trivyignore`, and configured `trivyignores: '.trivyignore.yaml'`.
   - Verified in `aquasecurity/trivy-action@57a97c7e7821a5776cebc9bb87c984fa69cba8f1`'s `action.yaml` and `entrypoint.sh` that a single `.yaml`/`.yml` file in `trivyignores` enters `# YAML mode` and exports `TRIVY_IGNOREFILE=".trivyignore.yaml"` directly without plain-text concatenation.
   - Created `.trivyignore.yaml` scoping `CVE-2026-111943` (`expired_at: 2026-12-01`) specifically to `paths: ["usr/local/bin/aws-lambda-rie"]` (the exact `Target` path reported by `trivy image --format json` for the AWS Lambda Runtime Interface Emulator binary in `public.ecr.aws/lambda/python:3.12`), and verified with `aquasec/trivy:0.69.3`:
     - (a) `usr/local/bin/aws-lambda-rie` in `cloudguard-backend:ci` is suppressed before expiry (exit `0`).
     - (b) The same `CVE-2026-111943` at another target path (`usr/local/bin/other-go-binary`) remains detectable and fails the scan (exit `1`).
     - (c) An expired exception (`expired_at: 2020-01-01`) no longer suppresses `usr/local/bin/aws-lambda-rie` and fails the scan (exit `1`).
6. **Phase 8.5 Security Regression Suite & Accurate Handoff (Round 3 Item 2):**
   - Reconciled every symbol name, helper name, and test node ID against actual source and `pytest --collect-only` output across `docs/development/phase8-autonomous-handoff.md`, `docs/development/current-task.md`, `SECURITY.md`, and `docs/THREAT_MODEL.md`, explicitly distinguishing deterministic/mocked tests (`test_security_regressions.py` and `test_callback_race.py`) from real PostgreSQL `cloudguard_app` tests (`test_rls_security.py`).
7. **Phase 8 Completion & Deployment Readiness Honesty Statement:**
   - Repository-level Phase 8 implementation and local verification are complete, **but Phase 8 as a whole is NOT claimed complete or production-deployment-ready** because external cloud resources, missing CDK stacks (API Lambda/API Gateway/WAF/Guardrail/CMK), Nova Pro's lack of native `CountTokens` support, Python 3.12 GitHub Actions runner execution, and live AWS runtime verification (including live Cognito Hosted UI integration) remain external/architectural items documented in Section 6.

---

## 2. Final Changed-File Inventory (46 Unstaged Paths)

### Initial 27 Unstaged Phase 8.4d Paths (Preserved)
1. `M .env.example` — Documented `RATE_LIMIT_TABLE_NAME` for API and deployed worker admission.
2. `M SECURITY.md` — Updated enforced controls and known limitations for Phase 8.4d, `AgentWorkflow` authority/admission, callback race readiness polling and rollback fail-closed behavior, migration `0005` audit, and hosted login PKCE/state/nonce/lifecycle tests.
3. `M backend/app/core/config.py` — Validated `RATE_LIMIT_TABLE_NAME` requirement for non-local API and worker composition.
4. `M backend/app/core/container.py` — Wired shared `WorkAdmission`, `DynamoDBRateLimitStore`, `InMemoryRateLimitStore`, `DynamoDBWorkLeaseStore`, and `InMemoryWorkLeaseStore` into worker and local containers, and passed `admission=work_admission` into `AgentWorkflow`.
5. `M backend/app/deployed_document_worker.py` — Enforced `WorkAdmission` (`WorkKind.INGESTION`) before repository construction and document processing claim.
6. `M backend/app/deployed_research_agent_worker.py` — Enforced `WorkAdmission` (`WorkKind.MODEL`) before `ResearchAgent` execution.
7. `M backend/app/deployed_reviewer_agent_worker.py` — Enforced `WorkAdmission` (`WorkKind.MODEL`) before `ReviewerAgent` execution.
8. `M backend/app/deployed_risk_agent_worker.py` — Enforced `WorkAdmission` (`WorkKind.MODEL`) before `RiskAgent` execution.
9. `M backend/app/document_worker.py` — Enforced `WorkAdmission` (`WorkKind.INGESTION`) in local document worker before opening a tenant DB session.
10. `M backend/app/security/rate_limits.py` — Isolated `api:v1:*`, `worker:v1:*`, and `slot:v1:*` key namespaces and defined worker admission budgets (`60/min` + `2` slots for model workers; `10/min` + `1` slot for ingestion; `600s` lease TTL).
11. `M backend/tests/unit/test_container.py` — Tested container wiring and non-local fail-closed behavior when `RATE_LIMIT_TABLE_NAME` is unset.
12. `M docs/development/current-task.md` — Updated Phase 8.4d, Phase 8.5, and special-review validation checkpoints.
13. `M docs/security/RATE_LIMITING.md` — Documented API counter namespace (`api:v1:*`) and worker admission relationship.
14. `M infrastructure/app.py` — Passed `rate_limit_table` from `RateLimitStack` into `IngestionStack` and `AgentWorkflowStack`.
15. `M infrastructure/stacks/agent_workflow_stack.py` — Wired `RATE_LIMIT_TABLE_NAME` and scoped `worker:v1:*` / `slot:v1:*` IAM grants (`grant_worker_admission`) to Research, Risk, and Reviewer Lambdas only.
16. `M infrastructure/stacks/ingestion_stack.py` — Wired `RATE_LIMIT_TABLE_NAME` and scoped `worker:v1:*` / `slot:v1:*` IAM grants (`grant_worker_admission`) to the document processor Lambda.
17. `M infrastructure/stacks/rate_limit_stack.py` — Defined `RateLimitStack.grant_admission` (`api:v1:*` `dynamodb:UpdateItem`) and module-level `grant_worker_admission` (`worker:v1:*` `dynamodb:UpdateItem` and `slot:v1:*` `dynamodb:PutItem`/`dynamodb:DeleteItem`) with `dynamodb:LeadingKeys` conditions.
18. `M infrastructure/tests/conftest.py` — Shared synthesis fixtures for `RateLimitStack`, `IngestionStack`, and `AgentWorkflowStack`.
19. `M infrastructure/tests/test_rate_limit_stack.py` — Verified `api:v1:*` and worker namespace IAM conditions on `RateLimitStack`.
20. `?? backend/app/adapters/aws/work_lease_store.py` — `DynamoDBWorkLeaseStore` conditional slot lease adapter (`try_acquire` via `PutItem` with absent/expired check, `release` via `DeleteItem` with owner-token check).
21. `?? backend/app/adapters/local/work_lease_store.py` — `InMemoryWorkLeaseStore` bounded in-memory slot lease adapter (`try_acquire`, `release`) for local development and tests.
22. `?? backend/app/ports/work_lease_store.py` — Port contract (`WorkLeaseStore`, `WorkLease`, `WorkLeaseUnavailableError`).
23. `?? backend/app/services/work_admission.py` — `WorkAdmission` coordinator (acquire slot first via `try_acquire`, debit minute counter second via `RateLimitStore.consume`, release slot via `release` on quota/store/execution failure).
24. `?? backend/tests/unit/test_work_admission.py` — Unit tests for `WorkAdmission`, slot expiry, owner-checked release, and store failures.
25. `?? backend/tests/unit/test_worker_admission_boundaries.py` — Boundary tests proving admission rejection precedes model/DB/S3 work across all four workers.
26. `?? docs/security/WORKER_ADMISSION.md` — Architecture, key-namespace, failure, and rollout documentation for worker admission.
27. `?? infrastructure/tests/test_worker_admission.py` — CDK assertions for worker admission env vars, IAM conditions, excluded approval Lambdas, and timeout `<` 600s lease invariant.

### Additional Phase 8.4, 8.5, Special-Review, and Round 2 / Round 3 Paths Added/Modified
28. `M .github/workflows/ci.yml` — Added blocking `Security regressions` step (`REQUIRE_SECURITY_DB_TESTS: '1'`), `infrastructure` job (`mypy stacks` + `pytest -ra`), real `pip-audit` (`backend` and `infrastructure`), blocking `npm audit --audit-level=high`, executable frontend tests (`npm test`), `gitleaks`, and `aquasecurity/trivy-action@57a97c7e7821a5776cebc9bb87c984fa69cba8f1` (`v0.35.0`) without `ignore-unfixed: true` using `trivyignores: '.trivyignore.yaml'`.
29. `M backend/app/adapters/aws/approval_execution_repository.py` — Added `CallbackDeliveryNotCommittedError` and bounded readiness polling (`_acquire_ready_approval`) that rolls back the `FOR UPDATE` lock before sleeping and fails closed immediately with sanitized `UpstreamError` if rollback fails.
30. `M backend/app/services/agent_workflow.py` — Made `admission: WorkAdmission` a required keyword dependency on `AgentWorkflow.__init__` and enforced both `Permission.DOCUMENT_READ` and `Permission.AI_QUERY` before entering `self._admission.admit(..., kind=WorkKind.MODEL)` in `AgentWorkflow.run`.
31. `M backend/pyproject.toml` — Registered the `security` pytest marker.
32. `M backend/tests/unit/test_agent_workflow.py` — Updated all workflow unit fixtures to pass required `WorkAdmission` and added regressions (`test_workflow_rejects_admin_before_admission_or_research`, `test_workflow_allows_authorized_roles_with_required_admission`, `test_workflow_enforces_work_admission_when_configured`, `test_workflow_fails_closed_when_admission_store_unavailable`; **13 passed**).
33. `M backend/tests/unit/test_aws_approval_execution_repository.py` — Added `test_callback_synchronization_race_recovers_after_bounded_poll` and `test_readiness_rollback_failure_aborts_immediately_without_second_poll` (**8 passed**).
34. `M docs/THREAT_MODEL.md` — Updated threat register (`CG-14`, `CG-16`, `CG-17`, `CG-18`, `CG-21`) and Section 9 callback ordering description.
35. `M frontend/package.json` — Added `"test": "vitest run"`, test dependencies (`vitest`, `jsdom`, `@testing-library/react`, `@testing-library/dom`), and security `overrides` (`postcss: ^8.5.23`, `brace-expansion: ^1.1.21`).
36. `M frontend/package-lock.json` — Updated lockfile (`0` critical, `0` high vulnerabilities).
37. `M frontend/src/app/login/page.tsx` — Async PKCE login initiation and error handling on `/login`.
38. `M frontend/src/components/auth/AuthProvider.tsx` — Synchronous callback capture (`captureHostedCallback`), single-flight PKCE exchange (`runSingleFlightCallbackExchange`), cancellation/epoch lifecycle protection (`cancelled` + `sessionEpochRef` + `routerRef`), and URL parameter scrubbing after active `/auth/config` resolution.
39. `M frontend/src/lib/auth.ts` — RFC 7636 PKCE (`S256`, 48-byte verifier), 24-byte `state` and `nonce`, single-use `sessionStorage` transaction state (`cloudguard.auth.pkce`, 10-minute TTL), single-flight callback coordinator (`runSingleFlightCallbackExchange`), `/oauth2/token` exchange, and ID token claim validation.
40. `?? .trivyignore.yaml` — Component-scoped, time-bounded Trivy YAML exception (`id: CVE-2026-111943`, `paths: ["usr/local/bin/aws-lambda-rie"]`, `expired_at: 2026-12-01`) with justification statement.
41. `?? backend/tests/security/test_callback_race.py` — 6 mocked/in-memory callback and RDS Data API race tests in `TestCallbackRace`.
42. `?? backend/tests/security/test_rls_security.py` — 7 real PostgreSQL security tests executed as `cloudguard_app` `NOBYPASSRLS` plus 1 migration-file sequence check (`test_published_migrations_sequence_is_intact`), gated by `require_security_database()`.
43. `?? backend/tests/security/test_security_regressions.py` — 94 deterministic service, route, schema, and AST security tests across 7 test classes.
44. `?? docs/development/phase8-autonomous-handoff.md` — This handoff document.
45. `?? frontend/src/components/auth/AuthProvider.test.tsx` — 6 executable Vitest component tests for `AuthProvider` hosted-login callback races, cleanup, failure, logout epoch invalidation, and PKCE challenge generation.
46. `?? frontend/vitest.config.mts` — Vitest (`jsdom` + automatic JSX runtime) configuration for `frontend/`.

---

## 3. Requirement-by-Requirement Status Matrix

| Subphase / Item | Requirement | Status | Implementation & Verification Evidence |
|---|---|---|---|
| **Phase 8.4a** | Per-document ingestion resource budgets (bytes, S3 stream reads, PDF pages, extracted chars, chunks) | **Implemented & Validated** | `backend/app/security/ingestion_limits.py`, `docs/security/INGESTION_BUDGETS.md`, unit + integration tests |
| **Phase 8.4b** | Centralized API rate limiting (`api:v1:*`) for peer login/auth and verified user/tenant route quotas + DynamoDB stack | **Implemented & Validated (Code/CDK)** | `backend/app/security/rate_limits.py`, `InMemoryRateLimitStore`, `DynamoDBRateLimitStore`, `RateLimitStack.grant_admission`, `docs/security/RATE_LIMITING.md` (API Lambda stack/IAM wiring remains an external deployment item) |
| **Phase 8.4c** | Model envelope/output budgets (`MAX_GENERATION_INPUT_BYTES`, `MAX_GENERATION_OUTPUT_BYTES`, `MAX_GENERATION_OUTPUT_TOKENS`, `require_text_budget`, `require_generation_envelope`, `checked_embedding_inputs`), single-attempt Bedrock SDK config, bounded Lambda throttle retries, reserved worker concurrency | **Implemented & Validated** | `backend/app/security/model_budgets.py`, `docs/security/RESOURCE_BUDGETS.md`, `AgentWorkflowStack`, `IngestionStack` |
| **Phase 8.4d** | Shared per-tenant worker admission (`WorkAdmission`, `60/min` + 2 slots for model workers, `10/min` + 1 slot for ingestion, `600s` lease, `worker:v1:*` & `slot:v1:*` IAM isolation via `grant_worker_admission`) | **Implemented & Validated** | `backend/app/services/work_admission.py`, `InMemoryWorkLeaseStore`, `DynamoDBWorkLeaseStore`, `test_work_admission.py`, `test_worker_admission_boundaries.py`, `infrastructure/tests/test_worker_admission.py` |
| **Review Item 1 (Workflow Authority & Admission)** | Require both `DOCUMENT_READ` and `AI_QUERY` plus mandatory `admission: WorkAdmission` on `AgentWorkflow`; document local workflow vs deployed worker-step admission | **Implemented & Validated (R2)** | `backend/app/services/agent_workflow.py`, `backend/tests/unit/test_agent_workflow.py` (13 passed), `TestPromptInjectionAndUntrustedEvidence::test_agent_workflow_enforces_dual_permissions_and_admission_end_to_end` |
| **Review Item 2 (Hosted-Login Lifecycle Race)** | Fix cancelled `/auth/config` callback ownership race, prevent stale session restoration after cleanup/logout, keep PKCE single-use in `cloudguard.auth.pkce`, keep Strict Mode enabled, and run frontend tests in CI | **Implemented & Validated (R2)** | `frontend/src/components/auth/AuthProvider.tsx`, `frontend/src/components/auth/AuthProvider.test.tsx` (6 passed via `npm test`), `.github/workflows/ci.yml` |
| **Review Item 3 (Callback Rollback Safety)** | Do not swallow `UpstreamError` on `_rollback` in `_acquire_ready_approval`; never start a second readiness poll unless rollback succeeded; fail closed with sanitized `UpstreamError` | **Implemented & Validated (R2)** | `backend/app/adapters/aws/approval_execution_repository.py`, `test_readiness_rollback_failure_aborts_immediately_without_second_poll`, `TestCallbackRace::test_execution_repository_fails_closed_when_readiness_rollback_fails` |
| **Review Item 4 / R3 Item 1 (Component-Scoped Container Scanning)** | Pin `aquasecurity/trivy-action@57a97c7e7821a5776cebc9bb87c984fa69cba8f1` (`v0.35.0`), remove `ignore-unfixed: true` and global `.trivyignore`, use `.trivyignore.yaml` scoped to `paths: ["usr/local/bin/aws-lambda-rie"]` with `expired_at: 2026-12-01`, and verify target-path and expiry behavior with `aquasec/trivy:0.69.3` | **Implemented & Validated (R3)** | `.github/workflows/ci.yml`, `.trivyignore.yaml`, verified (a) `usr/local/bin/aws-lambda-rie` suppressed before expiry (exit `0`), (b) same CVE at `usr/local/bin/other-go-binary` detected (exit `1`), and (c) expired rule (`2020-01-01`) detected (exit `1`) |
| **Review Item 5 / R3 Item 2 (Accurate Handoff & Docs)** | Generate test references from actual source/pytest collection, use real `grant_admission` and `model_budgets.py` symbols, distinguish mocked race tests from real DB tests, and reconcile all docs | **Implemented & Validated (R3)** | `docs/development/phase8-autonomous-handoff.md`, `docs/development/current-task.md`, `SECURITY.md`, `docs/THREAT_MODEL.md` |

---

## 4. Validation Commands and Results

### 4.1 Checks Executed in Round 3

| Check | Exact Command Executed in Round 3 | Actual Result |
|---|---|---|
| **Trivy Action `v0.35.0` YAML Interface Verification** | Inspected `action.yaml` and `entrypoint.sh` at `aquasecurity/trivy-action@57a97c7e7821a5776cebc9bb87c984fa69cba8f1` | Verified `trivyignores: '.trivyignore.yaml'` matches `*.yml\|*.yaml`, enters `# YAML mode`, and sets `export TRIVY_IGNOREFILE=".trivyignore.yaml"` directly without plain-text concatenation; default Trivy version is `v0.69.3` |
| **Trivy Test A: Intended Emulator Finding Suppressed Before Expiry** | `docker run --rm -v //var/run/docker.sock:/var/run/docker.sock -v D:/cloudguard-ai/cloudguard-ai-phase2/cloudguard-ai:/work aquasec/trivy:0.69.3 image --scanners vuln --severity HIGH,CRITICAL --ignorefile /work/.trivyignore.yaml --exit-code 1 cloudguard-backend:ci` | `usr/local/bin/aws-lambda-rie \| gobinary \| 0` — clean, exit `0` |
| **Trivy Test B: Same CVE at Another Target Path Remains Detectable** | Built temporary image `cloudguard-backend:trivy-path-test` (`FROM cloudguard-backend:ci` + `RUN cp /usr/local/bin/aws-lambda-rie /usr/local/bin/other-go-binary`) and ran `aquasec/trivy:0.69.3 image --scanners vuln --severity HIGH,CRITICAL --ignorefile /work/.trivyignore.yaml --exit-code 1 cloudguard-backend:trivy-path-test` | `usr/local/bin/aws-lambda-rie` suppressed (`0`), `usr/local/bin/other-go-binary` flagged with `CVE-2026-111943` (`HIGH: 1`), exit `1` |
| **Trivy Test C: Expired Exception No Longer Suppresses Finding** | Ran `aquasec/trivy:0.69.3 image --scanners vuln --severity HIGH,CRITICAL --ignorefile /root/.cache/expired.trivyignore.yaml --exit-code 1 cloudguard-backend:ci` with `expired_at: 2020-01-01` | `usr/local/bin/aws-lambda-rie` flagged with `CVE-2026-111943` (`HIGH: 1`), exit `1` |
| **Pytest Security Node Collection** | `Set-Location D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai\backend; python -m pytest tests/security --collect-only -q` | `108` tests collected (`test_callback_race.py: 6`, `test_rls_security.py: 8`, `test_security_regressions.py: 94`), exit `0` |
| **Whitespace, Staged Index, and Migration Immutability Checks** | `Set-Location D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai; git diff --check; git diff --cached --name-only; git diff --name-only backend/migrations/versions` | Clean (`0` whitespace errors, `0` staged files, `0` modified migration files, exit `0`) |

### 4.2 Historical Validation Evidence from Round 2 (Preserved; Unchanged Application Code Not Rerun in R3)

| Area (Round 2 Historical Run) | Exact Command Executed in Round 2 | Round 2 Result |
|---|---|---|
| **Infrastructure Types** | `Set-Location D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai\infrastructure; python -m mypy stacks` | `Success: no issues found in 5 source files` (exit `0`) |
| **Infrastructure Tests** | `Set-Location D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai\infrastructure; python -m pytest -ra` | `47 passed in 219.52s` (exit `0`) |
| **Backend Lint** | `Set-Location D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai\backend; python -m ruff check .` | `All checks passed!` (exit `0`) |
| **Backend Types** | `Set-Location D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai\backend; python -m mypy app tests/security` | `Success: no issues found in 147 source files` (exit `0`) |
| **Security Regression Suite** | `Set-Location D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai\backend; $env:DB_USER="cloudguard_app"; $env:DB_PASSWORD="cloudguard_app"; $env:RUN_DB_TESTS="1"; $env:REQUIRE_SECURITY_DB_TESTS="1"; python -m pytest tests/security -ra -v` | `108 passed in 1.56s` (exit `0`) |
| **Full Backend Suite + Coverage** | `Set-Location D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai\backend; $env:DB_USER="cloudguard_app"; $env:DB_PASSWORD="cloudguard_app"; $env:RUN_DB_TESTS="1"; $env:REQUIRE_SECURITY_DB_TESTS="1"; python -m pytest -ra --tb=short --cov=app --cov-report=term-missing` | `1223 passed, 92% total coverage in 37.49s` (`0` failed, `0` skipped, exit `0`) |
| **Python Dependency Audit (`backend` + `infrastructure`)** | `python -m pip_audit -r C:/Users/User/AppData/Local/Temp/backend-audit-reqs.txt; python -m pip_audit -r infrastructure/requirements.txt` | `No known vulnerabilities found` for both `backend` and `infrastructure` (exit `0`) |
| **Frontend Component & Lifecycle Tests** | `Set-Location D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai\frontend; npm test` | `1 passed (1) Test Files, 6 passed (6) Tests` in `src/components/auth/AuthProvider.test.tsx` (exit `0`) |
| **Frontend High/Critical Audit, Lint, Types, Build** | `Set-Location D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai\frontend; npm audit --audit-level=high; npm run lint; npm run typecheck; npm run build` | `0 high, 0 critical` (`14 moderate` dev-only `eslint`/`minimatch` advisories remain); ESLint, `tsc --noEmit`, and Next.js static export clean (exit `0`) |

---

## 5. Exact Security Regression Inventory (`backend/tests/security/` — 108 Collected Tests)

### 5.1 `backend/tests/security/test_callback_race.py` (6 Deterministic Mocked / In-Memory Tests in `TestCallbackRace`)

> **Test-Double Scope Note:** These 6 tests use deterministic in-memory doubles (`FakeCallbackClaim`, `FakeRecoveryRepository`, `FakeCallbackPort`, and `RacingRDSDataClient` / `RollbackFailingRaceClient`) to simulate Step Functions callback outcomes and RDS Data API transaction responses (`begin_transaction`, `execute_statement`, `rollback_transaction`, `commit_transaction`). They are mocked transaction/protocol tests, **not** multi-connection real PostgreSQL concurrency tests.

- `TestCallbackRace::test_delivery_success_but_marker_commit_fails_raises` — Verifies `ApprovalCallbackService.resume_decided` raises `RuntimeError` (`"delivery marker"`) when `FakeCallbackPort.send_decision` succeeds (`callback.sent is True`) but `FakeRecoveryRepository.mark_callback_delivered` returns `False`.
- `TestCallbackRace::test_callback_failure_releases_claim_without_marking_delivered` — Verifies `ApprovalCallbackService.resume_decided` calls `release_callback_claim` (`repo.release_called is True`) when `FakeCallbackPort(should_fail=True)` raises `RuntimeError`.
- `TestCallbackRace::test_never_marks_delivered_before_provider_success` — Verifies `ApprovalCallbackService.resume_decided` invokes `send_decision` strictly before `mark_callback_delivered` (`call_order == ["send_decision", "mark_delivered"]`).
- `TestCallbackRace::test_execution_repository_reconciles_inflight_callback_commit_race` — Exercises `AuroraDataAPIApprovalExecutionRepository.execute_for_workflow` against `RacingRDSDataClient(deliver_on_attempt=3)`, verifying `rollback_transaction` is called before each sleep, `select_attempts == 3`, `len(slept) == 2`, `insert_count == 1`, `result.disposition is ActionExecutionDisposition.SUCCEEDED`, and final `commit_transaction`.
- `TestCallbackRace::test_execution_repository_fails_closed_when_callback_never_commits` — Exercises `AuroraDataAPIApprovalExecutionRepository.execute_for_workflow` against `RacingRDSDataClient(deliver_on_attempt=None)` with `max_readiness_attempts=3`, verifying `CallbackDeliveryNotCommittedError` is raised after `select_attempts == 3` and `len(slept) == 2`, with `insert_count == 0` and final `rollback_transaction`.
- `TestCallbackRace::test_execution_repository_fails_closed_when_readiness_rollback_fails` — Exercises `AuroraDataAPIApprovalExecutionRepository.execute_for_workflow` against `RollbackFailingRaceClient(deliver_on_attempt=2)` whose `rollback_transaction` raises `RuntimeError("sensitive-rds-rollback-failure")`, verifying a sanitized `UpstreamError` is raised (`"sensitive-rds-rollback-failure"` absent from error text) after a single attempt (`select_attempts == 1`, `slept == []`, `insert_count == 0`, `calls.count("begin_transaction") == 1`).

### 5.2 `backend/tests/security/test_rls_security.py` (7 Real PostgreSQL `cloudguard_app` Tests + 1 Migration-File Sequence Check)

> **Database Scope Note:** Module fixture `_seed_security_tenants` calls `require_security_database()` (which fails closed when `REQUIRE_SECURITY_DB_TESTS=1` and `RUN_DB_TESTS != "1"`), seeds `TENANT_A` and `TENANT_B` via `database_admin_session()`, and seeds tenant rows (`users`, `documents`, `approvals`) via `tenant_session(...)` (`SET LOCAL app.current_organization_id`). The first 7 tests query live PostgreSQL as runtime role `cloudguard_app`; the 8th inspects `backend/migrations/versions/` on disk.

- `test_runtime_role_is_nobypassrls_and_non_superuser` — Queries `pg_roles` for `current_user` and asserts `("cloudguard_app", False, False, False, False)` for `(current_user, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole)`.
- `test_every_tenant_table_has_enabled_and_forced_rls_in_catalog` — Queries `pg_class` joined with `pg_namespace` (`nspname = 'public'`, `relkind = 'r'`, excluding non-tenant tables `alembic_version`, `organizations`, `compliance_frameworks`, `compliance_controls`) and asserts the table set equals `EXPECTED_FORCED_RLS_TENANT_TABLES` (11 tables: `users`, `documents`, `document_chunks`, `compliance_assessments`, `control_assessments`, `evidence_references`, `assessment_score_snapshots`, `score_overrides`, `approvals`, `security_reviews`, `approval_audit_events`) with `relrowsecurity is True` and `relforcerowsecurity is True` on each.
- `test_runtime_role_is_denied_truncate_and_audit_sequence_usage` — Asserts `TRUNCATE TABLE documents` and `SELECT nextval('approval_audit_events_id_seq')` fail with `permission denied`, and `DELETE FROM approval_audit_events` fails with `append-only|immutable|permission denied`.
- `test_rls_blocks_untenanted_and_cross_tenant_access` — Asserts `untenanted_session()` returns `[]` for `SELECT id FROM documents` and `SELECT id FROM approvals`, and `tenant_session(TENANT_A)` returns `None` when selecting `DOC_B` or `APPROVAL_B` by ID.
- `test_rls_with_check_rejects_cross_tenant_writes` — Asserts `INSERT INTO documents` with `organization_id = TENANT_B` inside `tenant_session(TENANT_A)` is rejected by the RLS `WITH CHECK` policy.
- `test_approval_context_immutability_and_delete_prohibition` — Asserts `UPDATE approvals SET task_token = 'forged-token'` is rejected as `immutable` and `DELETE FROM approvals` is rejected with `permission denied`.
- `test_migration_0005_expected_indexes_exist_in_postgresql` — Queries `pg_indexes` (`schemaname = 'public'`) and asserts all 17 `(tablename, indexname)` pairs in `EXPECTED_MIGRATION_0005_INDEXES` exist.
- `test_published_migrations_sequence_is_intact` — Asserts `backend/migrations/versions/` contains the exact 12 migration files `0001_initial_schema.py` through `0012_runtime_role_privileges.py`.

### 5.3 `backend/tests/security/test_security_regressions.py` (94 Deterministic Application-Boundary Tests Across 7 Classes)

- **`TestRBACAndRouteBoundaries` (56 collected tests):**
  - `test_exhaustive_role_permission_matrix` (54 parametrized tests: 3 `Role` × 18 `Permission` values checking `has_permission(principal, permission)` against `EXPECTED`).
  - `test_role_permissions_constant_matches_matrix` (1 test verifying `ROLE_PERMISSIONS == EXPECTED`).
  - `test_protected_routes_enforce_role_segregation_at_http_boundary` (1 test using FastAPI `TestClient` with `get_db_session` overridden by `AsyncMock()` to verify HTTP 403/200 role boundaries on `/api/v1/system/config`, `/api/v1/rag/query`, `/api/v1/approvals`, `/api/v1/approvals/{id}/decision`, `/api/v1/compliance/assessments/{id}/finalize`, and `/api/v1/compliance/assessments/{id}/override`).
- **`TestForgedIdentityAndTenantClaims` (5 collected tests):**
  - `test_token_with_no_recognised_role_group_rejected`
  - `test_token_with_ambiguous_role_groups_rejected`
  - `test_missing_or_malformed_organization_claim_rejected`
  - `test_resolve_role_ignores_unknown_and_rejects_empty`
  - `test_spoofed_tenant_headers_cannot_override_verified_jwt_claims`
- **`TestTenantIsolationAndClearance` (4 collected tests):**
  - `test_cross_organization_assertion_fails_closed`
  - `test_clearance_ceiling_is_strictly_role_derived`
  - `test_approval_service_blocks_cross_tenant_read_and_decision` (using `InMemoryTestApprovalRepository`)
  - `test_retrieval_and_rag_enforce_tenant_and_clearance_filtering` (using `MockEmbeddingProvider`, `InMemoryVectorStore`, `RetrievalService`, `RAGService`, and `ScriptedLLM`)
- **`TestPromptInjectionAndUntrustedEvidence` (23 collected tests):**
  - `test_adversarial_corpus_confinement_and_schema_rejection` (10 parametrized tests over `PROMPT_INJECTION_CORPUS` verifying `build_isolated_user_prompt` framing and Pydantic schema rejection on `ResearchToolIntent`, `ComplianceToolIntent`, `RiskModelOutput`, `ReviewerModelOutput`).
  - `test_rag_service_rejects_invented_source_citation_from_injected_chunk` (1 test).
  - `test_adversarial_corpus_end_to_end_agent_and_workflow_rejection` (10 parametrized tests over `PROMPT_INJECTION_CORPUS` executing `ResearchAgent.research`, `ComplianceAgent.analyze`, `RiskAgent.assess`, or `ReviewerAgent.review` with `ScriptedLLM` and verifying `UpstreamError`).
  - `test_compliance_agent_rejects_invented_control_id_and_source_label` (1 test executing `ComplianceAgent.analyze` with `SequenceLLM` returning an invented `control_id` and verifying `UpstreamError`).
  - `test_agent_workflow_enforces_dual_permissions_and_admission_end_to_end` (1 test verifying `AgentWorkflow.run` rejects `admin` with `AuthorizationError` before Research and succeeds for `analyst` with required `WorkAdmission`).
- **`TestInventedIdentifiersAndToolAllowlists` (3 collected tests):**
  - `test_tool_registry_enforces_per_agent_allowlists_and_budget`
  - `test_get_policy_arguments_reject_empty_or_oversized_control_ids`
  - `test_risk_and_reviewer_agents_reject_invented_chunk_ids`
- **`TestApprovalAndSideEffectBoundaries` (2 collected tests):**
  - `test_approval_rejects_non_manager_and_duplicate_decision` (verifying `Analyst`/`Admin` rejection, `task_token` absence from `PendingApproval`/`DecidedApproval` JSON dumps, and `ConflictError` on second decision using `InMemoryTestApprovalRepository`).
  - `test_side_effect_execution_rejects_supplied_action_and_replays_safely` (verifying `ApprovedActionExecutionWorkflowTaskInput` rejects extra `action` field and `ApprovedActionExecutionService` + `LocalSecurityReviewExecutor` returns `SUCCEEDED` then `REPLAYED`).
- **`TestAdmissionSensitiveDataAndLogging` (5 collected tests):**
  - `test_worker_admission_enforces_slots_and_fails_closed_on_store_outage`
  - `test_rate_limit_namespace_separation`
  - `test_sensitive_data_detector_and_model_output_gate`
  - `test_error_responses_never_echo_sensitive_inputs_or_tracebacks`
  - `test_application_logger_call_sites_forbid_raw_sensitive_keywords` (AST audit of `backend/app/**/*.py` `logger.*` calls disallowing `logger.exception`, dynamic event strings, and forbidden keyword arguments `error`, `detail`, `filename`, `file_name`, `storage_key`, `key`, `transaction_id`, `path`, `exc_info`, `stack_info`, `task_token`, `authorization`).

---

## 6. Open Blockers, Architectural Gaps, and Residual Risks

1. **No API Lambda / API Gateway / WAF CDK Stack in Repository:**
   - `infrastructure/` defines `IdentityStack`, `IngestionStack`, `AgentWorkflowStack`, and `RateLimitStack`. There is no CDK stack provisioning the FastAPI Lambda, API Gateway JWT authorizer, or AWS WAF. Wiring `RATE_LIMIT_TABLE_NAME` and `RateLimitStack.grant_admission(...)` to the deployed API compute role, configuring `TRUSTED_PROXY_CIDRS`, and enforcing edge WAF/authorizer rules remain Phase 11 deployment blockers.
2. **No Public Workflow-Start HTTP Endpoint:**
   - The repository has no HTTP route that calls `states:StartExecution` for the agent workflow. No synthetic endpoint was invented; `AgentWorkflow.run` enforces `Permission.DOCUMENT_READ`, `Permission.AI_QUERY`, and required `WorkAdmission` (`WorkKind.MODEL`) at the local workflow entry point, while deployed Step Functions `states:StartExecution` IAM scoping and queued-execution admission remain deployment items.
3. **Nova Pro Native Input-Token Counting (`CountTokens`) Limitation:**
   - The Reviewer agent uses Amazon Nova Pro (`us.amazon.nova-pro-v1:0`), which does not support Bedrock's native `CountTokens` API. Application controls bound UTF-8 byte/character envelopes (`MAX_GENERATION_INPUT_BYTES`, `MAX_GENERATION_OUTPUT_BYTES`, `MAX_GENERATION_OUTPUT_TOKENS`, `require_text_budget`, `require_generation_envelope`, and `checked_embedding_inputs` in `backend/app/security/model_budgets.py`); byte/character ceilings are not claimed to equal vendor tokenizer counts.
4. **Base Image `usr/local/bin/aws-lambda-rie` Binary Vulnerability (`CVE-2026-111943`):**
   - `public.ecr.aws/lambda/python:3.12` includes `usr/local/bin/aws-lambda-rie` compiled with Go `1.26.9` (`CVE-2026-111943`, HIGH, fixed in Go `1.26.10` / `1.27.3`). Because `aws-lambda-rie` is only the local container emulator and is not invoked by the AWS Lambda service in deployed functions, `.trivyignore.yaml` records a component-scoped, time-bounded exception (`id: CVE-2026-111943`, `paths: ["usr/local/bin/aws-lambda-rie"]`, `expired_at: 2026-12-01`) until AWS publishes a rebuilt base image or `aws-lambda-rie` is removed in a multi-stage build.
5. **Frontend Dev-Only `eslint` / `minimatch` Moderate Advisories:**
   - `npm audit --audit-level=high` passes with `0` critical and `0` high vulnerabilities. Fourteen moderate advisories remain in `minimatch <11.0.1` via `@eslint/config-array` / `@typescript-eslint/*` / `eslint-config-next@15.5.x` build-time lint dependencies; production runtime dependencies (`npm audit --omit=dev`) have `0` vulnerabilities.
6. **Local Python 3.14 vs CI Python 3.12 Runtime & Live Cloud Verification:**
   - Local validation ran on Python `3.14.3` with Docker PostgreSQL 16, local `aquasec/trivy:0.69.3`, Vitest, and synthesized CDK templates. Execution of `.github/workflows/ci.yml` on GitHub Actions (`python-version: '3.12'`) and live AWS evaluation of Bedrock Guardrails, live Cognito Hosted UI OAuth redirects/token exchange, real multi-node Aurora Data API callback contention, DynamoDB conditional leases under concurrent Lambda load, S3 upload size policies, and customer-managed KMS keys remain pre-production deployment checks.
