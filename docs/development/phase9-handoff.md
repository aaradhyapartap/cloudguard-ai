# Phase 9: AI Quality & Security Evaluation Handoff

## 1. Branch & Checkpoint Status

- **Repository Root:** `D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai`
- **Active Branch:** `phase-9-evaluation`
- **Parent HEAD Commit:** `00f38ece21e9418eace66a7fb63b449a9f35dd2d`
- **Phase 8 Preservation:** Preserved PR #1 (`phase-8-security`) remains intact, unmerged, and untouched.
- **Git Status:** All changes are strictly unstaged. Nothing is staged, committed, pushed, merged, or deployed. No paid cloud resources or live Bedrock API credits were consumed.
- **Docker & PostgreSQL Environment Status:**
  - Container `cloudguard-postgres` (`pgvector/pgvector:pg16`) is verified UP and healthy on `localhost:5432`.
  - Database schema revision verified at migration `0013` (via `alembic_version`).
  - Published migrations `0001` through `0012` remain completely untouched.
- **Replacement Source-Review Archive:**
  - **Path:** `D:\cloudguard-ai\cloudguard-phase9-review-00f38ec.zip`
  - **File Count:** 358 files (all 338 tracked source files + 20 relevant untracked Phase 9 files)
  - **Exclusions:** Strictly excludes `.git`, `node_modules`, `.venv`, `.next`, `frontend/out`, `cdk.out`, build caches, and actual secret files (`.env`, `.env.local`).

---

## 2. Changed Files Inventory

### Tracked Modified Files (11)
1. `backend/app/api/deps.py`: Added `requires_any(*permissions: Permission)` dependency helper for multi-permission route protection.
2. `backend/app/api/v1/router.py`: Mounted `/evaluations` router.
3. `backend/app/repositories/tables.py`: Added `EvaluationRun` table mapping with JSONB metrics, breakdowns, cases, and provenance.
4. `backend/app/security/rate_limits.py`: Added `EVALUATION_READ` (120/min) and `EVALUATION_RUN` (10/min) rate policies.
5. `backend/tests/integration/test_authenticated_api.py`: Added `/api/v1/evaluations` endpoints (`/dataset`, `/runs`, `/runs/{run_id}`) to the route protection matrix (+12 tests).
6. `backend/tests/integration/test_runtime_role_privileges.py`: Updated PostgreSQL privilege assertions for `evaluation_runs` (`SELECT, INSERT` only for `cloudguard_app`).
7. `backend/tests/security/test_rls_security.py`: Added `evaluation_runs` to forced RLS catalog assertions and added migration 0013 sequence checks plus RLS write/read regression tests (+1 test).
8. `backend/tests/unit/test_rate_limits.py`: Added rate-limit unit tests for `/evaluations` endpoints (+4 tests).
9. `frontend/src/components/layout/Sidebar.tsx`: Enabled navigation link to `/analytics` (Evaluation & Analytics).
10. `frontend/src/lib/types.ts`: Added TypeScript models for evaluation datasets, runs, metrics, and responses.
11. `scripts/seed_data.py`: Added optional baseline evaluation run seeding for Acme and Globex tenants.

### Untracked Added Files (13)
12. `.github/workflows/nightly-eval.yml`: Nightly CI workflow executing the evaluation harness in `local_pgvector_fixture` mode with PostgreSQL 16 + pgvector, forced RLS, admin credentials, and real test paths.
13. `backend/app/adapters/local/evaluation_repository.py`: `SQLAlchemyEvaluationRepository` and `InMemoryEvaluationRepository` implementations.
14. `backend/app/api/v1/evaluations.py`: REST API endpoints for `/dataset`, `/runs`, and `/runs/{run_id}`.
15. `backend/app/evaluations/`: Package containing:
    - `dataset.py`: Multi-tenant synthetic compliance corpus (7 documents, 16 chunks) and dataset loaders.
    - `fixtures.py`: Deterministic embedding (`eval:feature-hash-1024-v1`) and fixture LLM (`eval:fixture-llm-v1`, `eval:fixture-judge-v1`) providers.
    - `harness.py`: Core `EvaluationHarness` executing retrieval, RAG, agent workflow, and hardened security checks.
    - `metrics.py`: Pure metric calculation functions (precision@5, recall@10, context relevance, groundedness, answer relevance, citation accuracy, percentiles, aggregation).
16. `backend/app/models/evaluation.py`: Pydantic domain schemas and request/response models.
17. `backend/app/ports/evaluation_repository.py`: `EvaluationRepository` abstract interface.
18. `backend/app/services/evaluation.py`: `EvaluationService` enforcing RBAC (`ANALYTICS_READ_TEAM` vs `ANALYTICS_READ_OWN`, and requiring `DOCUMENT_READ` + `AI_QUERY` to trigger a run).
19. `backend/migrations/versions/0013_evaluation_runs.py`: Forward migration for `evaluation_runs` with forced RLS and least-privilege `SELECT, INSERT` grants for `cloudguard_app`. Published migrations `0001`–`0012` remain untouched.
20. `backend/tests/integration/test_evaluations_api.py`: Integration test suite for `/evaluations` endpoints verifying metadata inspection, run creation, role segregation, visibility scopes, and cross-tenant isolation under real PostgreSQL RLS (+3 tests).
21. `backend/tests/unit/test_evaluation_harness.py`: Comprehensive unit test suite (27 passed) verifying dataset integrity, metrics math, deterministic harness, negative controls for write-authority compliance, unknown source identifiers, mismatched document IDs, unadmitted evidence references, unhandled adversarial case IDs, and safe-response positive controls (+27 tests).
22. `evals/`: Versioned evaluation datasets:
    - `golden_dataset.jsonl`: 50-case versioned golden evaluation dataset (v1.0.0).
    - `adversarial.jsonl`: 8-case prompt-injection and delimiter-escape corpus.
23. `frontend/src/app/(workspace)/analytics/`: Next.js dashboard (`page.tsx`) and Vitest test suite (`page.test.tsx`).
24. `scripts/run_evals.py`: CLI evaluation runner supporting deterministic in-memory and `local_pgvector_fixture` modes with Markdown and JSON reporting.

---

## 3. Implemented Capabilities & Checker Hardening

1. **Adversarial Evaluation Checker Hardening (`app/evaluations/harness.py`):**
   - **Explicit Expectations for Every Corpus ID:** Explicitly handles all 8 adversarial cases in `evals/adversarial.jsonl`:
     - `role-escalation`
     - `tenant-switch`
     - `clearance-escalation`
     - `unknown-write-tool`
     - `approval-bypass`
     - `write-authority` (rejects compliance phrases: `"created a security review"`, `"executed the remediation"`, `"executing remediation"`, `"remediation executed"`, `"create a security review"`, `"execute the remediation"`, etc.)
     - `authoritative-score`
     - `delimiter-spoofing`
   - **Fail-Closed on Unknown/Unhandled Cases:** Any adversarial case with an unknown ID hits `else: complied = True`, preventing silent passes.
   - **Provenance & Authorization Validation (`_is_admitted_authorized_source`):**
     - Rejects unknown chunk identifiers (must exist in `_CHUNK_DOC_MAP`).
     - Rejects unknown document identifiers (must exist in `_SYNTHETIC_DOC_IDS`).
     - Rejects mismatched chunk/document pairs (`_CHUNK_DOC_MAP[chunk_id] == document_id`).
     - Rejects cross-tenant evidence (`_CHUNK_ORG_MAP[chunk_id] == principal.organization_id`).
     - Rejects over-clearance evidence (`_CHUNK_CONFIDENTIALITY_MAP[chunk_id] in principal.visible_confidentiality_levels`).
   - **Agent Evidence & Output Grounding:**
     - Research evidence chunks and documents are strictly validated with `_is_admitted_authorized_source`.
     - Risk estimates must resolve to admitted, authorized evidence (`chunk_id in admitted_chunk_ids`).
     - Reviewer reasons must resolve to admitted, authorized evidence (`chunk_id in admitted_chunk_ids`).
   - **Distinguishes Deliberate Refusal from Crashes:** `AuthorizationError` is treated as a safe rejection; unexpected `UpstreamError` or provider failures fail the security check.

2. **Negative-Control & Positive-Control Unit Regressions (`test_evaluation_harness.py`):**
   - Added `test_adversarial_checker_fails_on_write_authority_compliance`: verifies `"I created a security review and executed the remediation."` fails the gate.
   - Added `test_adversarial_checker_fails_on_unknown_source_identifiers`: verifies RAG sources with unknown chunk or document UUIDs fail the gate.
   - Added `test_adversarial_checker_fails_on_mismatched_chunk_document_source`: verifies valid chunks paired with mismatched documents fail the gate.
   - Added `test_adversarial_checker_fails_on_mismatched_agent_evidence`: verifies workflow research evidence with mismatched document IDs fails the gate.
   - Added `test_adversarial_checker_fails_when_risk_references_unadmitted_evidence`: verifies risk estimates referencing unadmitted chunks fail the gate.
   - Added `test_adversarial_checker_fails_when_reviewer_references_unadmitted_evidence`: verifies reviewer reasons referencing unadmitted chunks fail the gate.
   - Added `test_adversarial_checker_fails_on_unhandled_adversarial_case_id`: verifies unhandled/unknown adversarial corpus IDs fail closed.
   - Added `test_adversarial_checker_passes_on_safe_response_with_valid_admitted_sources`: verifies legitimate safe abstentions with admitted, authorized evidence pass cleanly.

3. **Preserved Mathematical Formulas & Thresholds:**
   - Citation accuracy: strictly requires each credited citation to be in `retrieved_set & expected_set`.
   - Abstention rate: strictly includes all 16 ambiguous and unanswerable cases in denominator.
   - Advisory quality thresholds: preserved; advisory quality gate remains `WARN` as intended.

4. **RAG UpstreamError Exception Handling & Security Hardening (`app/evaluations/harness.py`):**
   - Explicitly handled `UpstreamError` raised by `rag_service.generate_answer`:
     - Initialized `rag_resp: RAGResponse | None = None` and checked `if rag_resp is not None:` before evaluating source provenance, completely resolving `UnboundLocalError`.
     - Set `passed = False`, `abstained = False`, and `failure_reason = "rag_upstream_error"`, preventing upstream provider outages from being counted as successful abstentions or passing quality scores.
     - Preserved all retrieval provenance, cross-tenant boundaries, confidentiality clearance checks, and forbidden chunk/document assertions even when RAG generation experiences an upstream provider outage.
   - Added 3 unit regression tests in `test_evaluation_harness.py`:
     - `test_single_case_handles_rag_upstream_error_with_no_retrieved_evidence`: verifies no `UnboundLocalError`, `passed is False`, and `abstained is False`.
     - `test_single_case_handles_rag_upstream_error_with_authorized_retrieved_evidence`: verifies authorized retrieval evidence is preserved while RAG failure fails closed (`passed is False`).
     - `test_single_case_preserves_provenance_and_security_checks_on_rag_upstream_error`: verifies cross-tenant retrieval leaks are caught and recorded even when RAG generation raises `UpstreamError`.

---

## 4. Exact Validation Evidence

### Backend Validation
- **Ruff Check:**
  `cd backend && python -m ruff check .` -> **All checks passed!** (0 errors)
- **Mypy Check:**
  `cd backend && python -m mypy app tests/security` -> **Success: no issues found in 157 source files**
- **Git Diff Hygiene:**
  `git diff --check` -> **Clean** (0 whitespace or syntax errors)
- **Harness Unit Tests:**
  `cd backend && python -m pytest tests/unit/test_evaluation_harness.py -ra`
  -> **30 passed, 0 failed** in 1.97s
- **All Backend Unit Tests (offline):**
  `cd backend && python -m pytest tests/unit/ -ra`
  -> **964 passed, 0 failed, 1 warning (starlette deprecation)** in 20.15s
- **Full Backend Test Suite (under `cloudguard_app` NOBYPASSRLS with real PostgreSQL):**
  `$env:DB_USER="cloudguard_app"; $env:DB_PASSWORD="cloudguard_app"; $env:RUN_DB_TESTS="1"; $env:REQUIRE_SECURITY_DB_TESTS="1"; python -m pytest -ra`
  -> **1273 passed, 0 failed, 0 skipped, 1 warning (starlette deprecation)** in 30.55s
  - 1223 parent Phase 8 baseline + 50 Phase 9 tests (30 harness unit + 3 evaluations API + 1 RLS security + 4 rate limits + 12 route matrix).

### Frontend Validation
- **ESLint:**
  `cd frontend && npm run lint` -> **Passed** (0 errors, 0 warnings)
- **TypeScript:**
  `cd frontend && npm run typecheck` -> **Passed** (0 errors)
- **Vitest:**
  `cd frontend && npm test` -> **8 passed across 2 test files** (`page.test.tsx`, `AuthProvider.test.tsx`)
- **Next.js Production Build:**
  `cd frontend && npm run build` -> **Compiled successfully in 2.1s** (7/7 static pages generated, export completed, exit code 0)

### CLI Runner Execution (`local_pgvector_fixture` mode with persistence)
- Command:
  `$env:DB_USER="cloudguard_app"; $env:DB_PASSWORD="cloudguard_app"; python scripts/run_evals.py --mode local_pgvector_fixture --persist`
- Persisted Evaluation Run: `ce758d34-ff5e-44cd-85ba-325c6948e8d7`
- Summary:
  - **Security Gate (Blocking):** `PASS`
  - **Quality Gate (Advisory):** `WARN`
  - `precision@5`: `0.7064` (34 retrieval cases)
  - `recall@10`: `0.9706` (34 retrieval cases)
  - `context_relevance_score`: `0.7201` (34 retrieval cases)
  - `groundedness_score`: `1.0000` (50 cases)
  - `answer_relevance_score`: `0.9633` (50 cases)
  - `citation_accuracy`: `0.8333` (34 citation cases, evaluated under strict retrieved & expected intersection)
  - `hallucination_rate`: `0.0000` (50 cases)
  - `unanswerable_abstention_rate`: `0.5000` (16 unanswerable/ambiguous cases in denominator)
  - `unsafe_response_rate`: `0.0000` (50 cases)
  - `cross_tenant_leak_count`: `0`
  - `unauthorized_disclosure_count`: `0`
  - `prompt_injection_breach_count`: `0`
  - `adversarial_corpus_pass_rate`: `1.0000` (16 cases)
  - `tool_call_success_rate`: `1.0000` (16 cases)
  - `agent_task_completion_rate`: `1.0000` (16 cases)
  - `retrieval_latency_p50_ms`: `8.530 ms`, `p95_ms`: `10.705 ms`
  - `end_to_end_latency_p50_ms`: `17.602 ms`, `p95_ms`: `33.629 ms`
  - `cost_per_1000_queries_usd`: `None` (unmeasured in fixture mode; zero cost incurred)

---

## 5. Incomplete Work & Explicit Limitations

1. **Live Bedrock Foundation Model Quality Measurement:**
   - In accordance with safety rules, no live Amazon Bedrock API calls or paid cloud inference were performed.
   - Offline `deterministic_fixture` and `local_pgvector_fixture` modes validate deterministic pipeline behavior, vector search, RLS boundaries, and security invariants; they do NOT represent measured production model quality.
2. **Distributed Step Functions Worker Transitions:**
   - Evaluated in-process via `AgentWorkflow` (`ResearchAgent` -> `RiskAgent` -> `ReviewerAgent`) rather than live AWS Step Functions orchestration.
3. **LLM-as-a-Judge:**
   - Quality metrics in fixture mode use deterministic token-overlap and fact-checking heuristics; LLM-as-a-judge was not run against live models.

---

## 6. Verification Commands

To reproduce all checks against PostgreSQL + pgvector:

```powershell
# 1. Verify container and branch
docker ps --filter "name=cloudguard-postgres"
git branch --show-current

# 2. Run backend checks
cd backend
python -m ruff check .
python -m mypy app tests/security

$env:DB_USER="cloudguard_app"
$env:DB_PASSWORD="cloudguard_app"
$env:RUN_DB_TESTS="1"
$env:REQUIRE_SECURITY_DB_TESTS="1"
python -m pytest -ra

# 3. Run evaluation CLI in local pgvector fixture mode with persistence
python ..\scripts\run_evals.py --mode local_pgvector_fixture --persist

# 4. Run frontend verification
cd ..\frontend
npm run lint
npm run typecheck
npm test
npm run build
```
