# Phase 10: Observability Handoff

> **Historical Pre-Commit Checkpoint Notice:** This document records the pre-commit review checkpoint for Phase 10. Phase 10 was subsequently committed (`126d3b06e9521442534ac245397453e9eadcd536` + `.gitleaksignore` follow-up `4d7aa9c5b0f68d1858d757484e4df2f7057120e1`) and verified in GitHub Actions CI on branch `phase-10-observability` (Draft PR #3). For current repository status, see [`current-task.md`](current-task.md) and [`phase12-handoff.md`](phase12-handoff.md).

## 1. Branch & Checkpoint Status

- **Repository Root:** `D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai`
- **Active Branch:** `phase-10-observability`
- **Parent HEAD Commit:** `deb19cfe9edbcbf7100313000ca4d20425444e6c` (Phase 9 reviewed commit)
- **Draft PR Preservation:** Unmerged draft PRs #1 (`phase-8-security`) and #2 (`phase-9-evaluation`) remain intact, unmerged, and un-rebased.
- **Git Status:** All Phase 10 changes are strictly unstaged. Nothing is staged, committed, pushed, merged, or deployed. No paid cloud evaluations or live Bedrock API credits were consumed.
- **Docker & PostgreSQL Environment Status:**
  - Container `cloudguard-postgres` (`pgvector/pgvector:pg16`) is UP and healthy on `localhost:5432`.
  - Database schema revision verified at migration `0013` (via `alembic_version` query with user `cloudguard`).
  - Published migrations `0001` through `0013` remain completely untouched (no new migrations added or needed).
- **Source-Review Archive:**
  - **Path:** `D:\cloudguard-ai\cloudguard-phase10-review.zip`
  - **File Count:** 370 files (358 tracked + 12 untracked Phase 10 additions).
  - **Exclusions:** Strictly excludes `.git`, `node_modules`, `.venv`, `.next`, `frontend/out`, `cdk.out`, build caches, and actual secret files (`.env`, `.env.local`).

---

## 2. Changed Files Inventory

### Tracked Modified Files (31)
1. `backend/app/adapters/bedrock/llm.py`: Added `telemetry: TelemetryEmitter` injection; extracted available token usage and latency immediately at the Bedrock Converse response boundary before guardrail and output validation; preserved unknown usage provenance on successful responses without inventing `TokenUsage(0, 0)`; emitted `BedrockGuardrailInterventions`, `BedrockErrors`, model latency, and token usage on failure paths without double-counting on success paths; preserved all existing safety rejections and fail-closed behaviors.
2. `backend/app/api/deps.py`: Wrapped admission decision telemetry (`safe_telemetry_call`) for pre-auth peer rate limits, authenticated tenant/user rate limits, and token verification rejections.
3. `backend/app/api/middleware.py`: Instrument `RequestContextMiddleware` with bounded HTTP request latency, count, and error metrics (`HttpRequestCount`, `HttpRequestErrors`). Normalized unknown HTTP methods to `OTHER`, safely wrapped telemetry calls, and counted handled 5xx responses.
4. `backend/app/api/v1/rag.py`: Injected `container.telemetry` into `RetrievalService` and `RAGService`.
5. `backend/app/api/v1/retrieval.py`: Injected `container.telemetry` into `RetrievalService`.
6. `backend/app/core/container.py`: Wired shared telemetry into `_build_llm` and `_build_reviewer_llm` across `Container`, `ResearchAgentWorkerContainer`, `RiskAgentWorkerContainer`, and `ReviewerAgentWorkerContainer`. Implemented `_build_telemetry()` builder and wired telemetry into `build_work_admission`.
7. `backend/app/deployed_document_worker.py`: Wired `telemetry=_container.telemetry` into `DocumentProcessingService`.
8. `backend/app/deployed_research_agent_worker.py`: Wired `telemetry=_container.telemetry` into `RetrievalService` and `ResearchAgent`.
9. `backend/app/deployed_reviewer_agent_worker.py`: Wired `telemetry=_container.telemetry` into `ReviewerAgent`.
10. `backend/app/deployed_risk_agent_worker.py`: Wired `telemetry=_container.telemetry` into `RiskAgent`.
11. `backend/app/document_worker.py`: Wired `telemetry=_container.telemetry` into `build_work_admission` and `DocumentProcessingService`.
12. `backend/app/models/agent_workflow.py`: Supported nullable `usage: TokenUsage | None = None` in `AgentWorkflowResearchResult`.
13. `backend/app/models/ai.py`: Updated `TokenUsage` with nullable `input_tokens: int | None = None` and `output_tokens: int | None = None`, computed `total_tokens` property returning `None` on incomplete counts, and updated `GenerationResponse.usage` to `TokenUsage | None = None`.
14. `backend/app/models/compliance_agent.py`: Supported nullable `planning_usage: TokenUsage | None = None` and `evaluation_usage: TokenUsage | None = None` in `ComplianceAgentResult`.
15. `backend/app/models/research_agent.py`: Supported nullable `usage: TokenUsage | None = None` in `ResearchAgentResult`.
16. `backend/app/models/retrieval.py`: Added `cost: CostEstimate | None = None` to `RetrievalResponse`.
17. `backend/app/models/reviewer_agent.py`: Supported nullable `usage: TokenUsage | None = None` in `ReviewerAgentResult`.
18. `backend/app/models/risk_agent.py`: Supported nullable `usage: TokenUsage | None = None` in `RiskAgentResult`.
19. `backend/app/services/agent_workflow.py`: Instrument `AgentWorkflow.run()` with latency measurement, step operational signals (`agent_workflow_completed`), and error counters using `safe_telemetry_call`. Tokens are not double-counted (sub-agents emit their own).
20. `backend/app/services/compliance_agent.py`: Handled nullable `planning_usage` safely on early zero-evidence returns.
21. `backend/app/services/document_processing.py`: Added `telemetry` injection; recorded document embedding latency and input token usage via `safe_telemetry_call`.
22. `backend/app/services/rag.py`: Added `telemetry` injection; added query-boundary cost accounting (`record_query_cost`) summing component embedding and generation model calls; recorded model call token usage safely when usage is absent or incomplete; marked mixed known/unknown component costs as partial/unmeasured without fabricating zero costs; recorded model latency, `RAGErrors` counters, and zero-evidence status.
23. `backend/app/services/research_agent.py`: Added `telemetry` injection; recorded model call latency and token usage safely when usage is absent or incomplete via `safe_telemetry_call` before validation.
24. `backend/app/services/retrieval.py`: Added `telemetry` injection; recorded query embedding token usage, captured returned `CostEstimate`, passed cost in `RetrievalResponse`, and recorded retrieval latency, `RetrievalMatchesCount`, and `RetrievalErrors` counters via `safe_telemetry_call`.
25. `backend/app/services/reviewer_agent.py`: Added `telemetry` injection; recorded judge model latency and token usage safely when usage is absent or incomplete via `safe_telemetry_call` before validation.
26. `backend/app/services/risk_agent.py`: Added `telemetry` injection; recorded risk model latency and token usage safely when usage is absent or incomplete via `safe_telemetry_call` before validation.
27. `backend/app/services/work_admission.py`: Added `telemetry` injection; recorded worker slot and rate limit allowed/denied admissions with sanitized reasons (`slot_exhausted`, `rate_limit_exceeded`) using `safe_telemetry_call`. Guaranteed lease release in `finally`.
28. `backend/tests/unit/test_bedrock_chat_llm.py`: Added regressions for absent usage, one token-count field absent, explicit zero counts, and complete counts.
29. `backend/tests/unit/test_container.py`: Added container-level regressions for shared telemetry wiring across all builders and Guardrail intervention available usage recording once with original safety rejection.
30. `docs/development/current-task.md`: Updated Phase 10 implementation task tracker, review findings, and verification status.
31. `infrastructure/app.py`: Instantiated and wired `ObservabilityStack` into the CDK application with actual implemented constructs.

### Untracked Added Files (12)
23. `backend/app/adapters/aws/telemetry.py`: `CloudWatchEMFTelemetryEmitter` emitting CloudWatch Embedded Metric Format (EMF) JSON to stdout with explicit multi-dimension sets deduplicated by unordered `frozenset` identity, call-level vs. query-level metric separation, canary scrubbing, and stdout documentation.
24. `backend/app/adapters/local/telemetry.py`: `InMemoryTelemetryEmitter` for local development and deterministic test assertions with bounded retention (`deque(maxlen=max_records)`), per-call metrics (`CostMeasuredModelCalls`, `ModelCallEstimatedCostUSD`), and query-boundary cost recording (`record_query_cost`).
25. `backend/app/adapters/mock/telemetry.py`: `MockTelemetryEmitter` test double.
26. `backend/app/models/telemetry.py`: Pydantic domain models: `CostEstimate`, `TelemetryMetricRecord`, `OperationalSignal`, `AdmissionCategory`, `MetricUnit`, explicit allowlists (`ALLOWED_SAFE_ATTRIBUTE_KEYS`, `ALLOWED_COMPONENTS`, `ALLOWED_EXECUTION_MODES`, `ALLOWED_ERROR_TYPES`), legitimate closed-set precedence before broad canary checks (preserving `document_processing` and `token_verification_failed`), and strict bounded dimensionality mapping without `Ops*` escape hatches.
27. `backend/app/ports/telemetry.py`: `TelemetryEmitter` abstract port contract defining `record_latency`, `record_counter`, `record_token_usage`, `record_query_cost`, `record_admission_decision`, and `record_operational_signal`.
28. `backend/app/telemetry/__init__.py`: Telemetry package root.
29. `backend/app/telemetry/isolation.py`: `safe_telemetry_call` boundary isolation helper guaranteeing zero exception propagation.
30. `backend/app/telemetry/pricing.py`: Explicitly versioned Bedrock pricing catalog (`2026-03-01`), region (`us-east-1`), tier (`On-Demand`), and `estimate_query_cost()` calculator.
31. `backend/tests/unit/test_telemetry.py`: Comprehensive test suite (48 tests) verifying pricing calculations, bounded dimensionality validation, raising-emitter failure isolation, canary/privacy controls, safe attribute allowlists, query boundary vs model call accounting, mixed/partial query costs, EMF unordered dimension deduplication, Bedrock response boundary usage extraction before guardrail intervention, container-level telemetry wiring, and provenance preservation across absent, incomplete, explicit zero, and complete token counts without double-counting.
32. `docs/development/phase10-handoff.md`: This comprehensive Phase 10 handoff document.
33. `infrastructure/stacks/observability_stack.py`: CloudWatch operational alarms (7 alarms, including DynamoDB SystemErrors metric math across `UpdateItem`, `PutItem`, `DeleteItem`) and 5-row operational dashboard with supported Metric Math conditional `IF(measured > 0, cost / measured)` for honest cost-per-measured-query coverage.
34. `infrastructure/tests/test_observability_stack.py`: CDK assertions verifying dashboard widgets, privacy disclosures, alarms, bounded dimension rules, metric identity alignments, and synthesized Metric Math expression.

---

## 3. Telemetry Architecture & Technical Details

1. **Provider-Neutral Port & Adapter Architecture:**
   - Interface: `app.ports.telemetry.TelemetryEmitter` defines:
     - `record_latency`: operation latency in milliseconds.
     - `record_counter`: operational counters (invocations, errors, counts).
     - `record_token_usage`: per-model-call token usage and cost estimate (`CostMeasuredModelCalls`, `CostUnmeasuredModelCalls`, `ModelCallEstimatedCostUSD`).
     - `record_query_cost`: user query boundary cost and pricing coverage (`CostMeasuredQueries`, `EstimatedInferenceCostUSD`, `CostUnmeasuredQueries`, `CostPartialQueries`).
     - `record_admission_decision`: admission decisions with sanitized reasons.
     - `record_operational_signal`: structured lifecycle events with sanitized safe attributes.
   - Local Adapter: `InMemoryTelemetryEmitter` records metrics with strictly bounded retention (`deque(maxlen=self._max_records)`) and emits structured logs through `structlog`.
   - AWS Adapter: `CloudWatchEMFTelemetryEmitter` outputs CloudWatch Embedded Metric Format (EMF) to stdout.
     - *Performance and Cost Disclosure:* Emitting to stdout does not make synchronous network calls outside the VPC, but does incur stdout serialization and buffering overhead. Log lines are ingested by the CloudWatch Agent asynchronously, which incurs standard CloudWatch Logs ingestion charges ($0.50/GB) and custom metric charges ($0.30/metric/month for the first 10,000 metrics).
     - *Explicit Dimension Sets Deduplication:* `compute_emf_dimension_sets` deduplicates candidate dimension sets by unordered dimension identity (`frozenset(s)`), guaranteeing that no combination (such as `["Environment", "Category", "AdmissionDecision"]`) is emitted twice in different order.

2. **Total Boundary Failure Isolation (`safe_telemetry_call`):**
   - Telemetry calls at all service and middleware boundaries are wrapped with `safe_telemetry_call`.
   - If an emitter raises an exception, the exception is caught, logged as a structured warning, and swallowed.
   - Telemetry failures never crash requests, never mask upstream errors (such as `UpstreamError` or `RateLimitError`), never alter authentication or permissions, and never prevent slot lease release in `finally` blocks.

3. **Strict Bounded Dimensionality & Privacy Boundaries:**
   - **Allowed Metric Dimensions:** `Environment`, `Operation`, `ModelId`, `Category`, `Status`, `Stage`, `Severity`, `AdmissionDecision`.
   - **Prohibited Dimensions:** `user_id`, `document_id`, `request_id`, `organization_id`, `tenant_id`, `prompt`, `query`, `content`, `evidence`, `token`, `task_token`, `storage_key`, `s3_key`, `error_message`, `exception`.
   - Closed sets for all dimensions without `Ops*` escape hatches. Unknown operations, stages, or categories map to `"other"`; unknown HTTP methods map to `"OTHER"`; unknown statuses map to `"unknown"`; unknown model IDs map to `"unlisted_model"`; unknown admission reasons map to `"other"`.
   - Legitimate closed-set values (e.g. `"document_processing"` for Operation/Component, `"token_verification_failed"` for AdmissionReason) take precedence over broad substring canary checks so they are preserved accurately.
   - Unknown free-text safe attributes are dropped; only explicit allowlisted keys (`ALLOWED_SAFE_ATTRIBUTE_KEYS`) with closed-set or bounded numeric/boolean values are admitted.
   - Correlation IDs are preserved only as top-level structured event fields for tracing, never as metric dimensions.

4. **Model Call vs. User Query Accounting:**
   - **Model Call Level:** `record_token_usage` records token counts and emits `CostMeasuredModelCalls` or `CostUnmeasuredModelCalls` (and `ModelCallEstimatedCostUSD` when pricing is known) once per model invocation.
   - **User Query Level:** `record_query_cost` is called once at the user query boundary (`RAGService.generate_answer`).
     - When both embedding and generation calls have known catalog pricing: `EstimatedInferenceCostUSD` is emitted as their combined sum, and `CostMeasuredQueries` is incremented once (1.0).
     - When component costs are mixed (one known, one unknown/fixture): the query cost is marked partial (`is_partial=True, is_measured=False`), emitting `CostUnmeasuredQueries = 1.0` and `CostPartialQueries = 1.0`. The incomplete cost is NOT emitted as a total query cost to avoid corrupting average cost-per-query.
     - When all components are unmeasured/fixture: `CostUnmeasuredQueries = 1.0`.

5. **Supported CloudWatch Metric Math for Cost Per Query:**
   - In `infrastructure/stacks/observability_stack.py`, the cost-per-measured-query metric math uses the supported conditional expression:
     `expression="IF(measured > 0, cost / measured)"`
   - Periods with zero or missing measured queries evaluate to no data / null rather than fabricating a zero cost or deflating the metric.

6. **Bedrock Adapter Response Boundary Usage Extraction & Container Wiring:**
   - In `BedrockLLMProvider.generate`, token usage and latency metrics are extracted from the Bedrock Converse response immediately at the response boundary before checking guardrail intervention or output text validation.
   - On guardrail intervention (`stop_reason == "guardrail_intervened"` or `guardrailAction` intervened), the adapter records consumed tokens, latency, and increments `BedrockGuardrailInterventions`, then raises `UpstreamError("The model generation request was blocked by safety controls.")`.
   - The shared telemetry emitter is explicitly wired into `BedrockLLMProvider` through `_build_llm` and `_build_reviewer_llm` and all container builders (`build_container`, `build_research_agent_worker_container`, `build_risk_agent_worker_container`, `build_reviewer_agent_worker_container`), ensuring runtime container construction enables adapter-side rejection and error telemetry.
   - On success, the adapter returns `GenerationResponse` with exact extracted usage (or `None` if absent, without inventing `TokenUsage(0, 0)`).
   - Unknown or incomplete token counts (e.g. one field absent) remain unmeasured (`estimated_inference_cost_usd = None`, `is_measured = False`).
   - Known zero usage (`input_tokens == 0 and output_tokens == 0`) for catalog models is measured as $0.00 (`estimated_inference_cost_usd = 0.0`, `is_measured = True`), remaining strictly distinguishable from absent usage.
   - Affected user queries with unmeasured or partial generation usage remain partial/unmeasured at the query boundary (`CostUnmeasuredQueries = 1.0`, `CostPartialQueries = 1.0`).
   - Model-call and query coverage are verified with zero double-counting.

7. **CDK Observability Stack Resources:**
   - **7 Operational Alarms:**
     1. `IngestionDlqVisibleMessagesAlarm` (messages in DLQ >= 1, `NOT_BREACHING`)
     2. `IngestionExecutionFailedAlarm` (failed ingestion executions >= 1, `NOT_BREACHING`)
     3. `ProcessorLambdaErrorsAlarm` (document processor Lambda errors >= 1, `NOT_BREACHING`)
     4. `AgentWorkflowExecutionFailedAlarm` (failed agent workflow executions >= 1, `NOT_BREACHING`)
     5. `AgentWorkflowThrottledAlarm` (Step Functions throttles >= 1, `NOT_BREACHING`)
     6. `RateLimitTableSystemErrorsAlarm` (DynamoDB system errors via metric math `m0 + m1 + m2` for operations `UpdateItem`, `PutItem`, `DeleteItem` >= 1, `NOT_BREACHING`)
     7. `AdmissionDenialsSpikeAlarm` (custom metric `AdmissionDenied` with `Environment` dimension >= 50 in 5 min, `NOT_BREACHING`)
   - **CloudWatch Operational Dashboard (`CloudGuard-Operations-{env}`):**
     - Row 0: Privacy boundary declaration banner.
     - Row 1: Document Ingestion State Machine Executions & DLQ Messages / Processor Errors.
     - Row 2: Agent Workflow Executions, Step Health & Lambda Errors.
     - Row 3: API Admission Decisions & Rate Limit DynamoDB Consumed Capacity Units.
     - Row 4: RAG & Retrieval Latency Percentiles (p50/p95), Model Token Usage / Total Cost, and Estimated Cost Per Query & Pricing Coverage (`IF(measured > 0, cost / measured)`).
     - Row 5: Telemetry Notes & Cost Coverage documentation text widget.

---

## 4. Exact Validation Results

| Test Category / Suite | Command | Result | Notes |
| --- | --- | --- | --- |
| **Backend Full Test Suite** | `$env:DB_USER="cloudguard_app"; $env:DB_PASSWORD="cloudguard_app"; $env:RUN_DB_TESTS="1"; $env:REQUIRE_SECURITY_DB_TESTS="1"; python -m pytest backend -ra` | **1,327 passed**, 0 failed, 0 skipped in 37.63s | Executed against real PostgreSQL 16 + pgvector under `cloudguard_app` NOBYPASSRLS role. |
| **Telemetry Unit Suite** | `python -m pytest backend/tests/unit/test_telemetry.py` | **48 passed**, 0 failed, 0 skipped in 0.92s | Verified query boundary vs call accounting, partial costs, EMF frozenset dedup, safe attribute allowlists, container wiring, and provenance across absent/incomplete/zero/complete tokens. |
| **Focused Unit Suites** | `python -m pytest backend/tests/unit/test_container.py backend/tests/unit/test_bedrock_chat_llm.py backend/tests/unit/test_telemetry.py` | **85 passed**, 0 failed, 0 skipped in 1.64s | Verified container builders, Bedrock chat LLM usage extraction variants, and telemetry integration. |
| **CDK Infrastructure Tests** | `cd infrastructure && python -m pytest` | **52 passed**, 0 failed, 0 skipped in 217.87s | Verified `ObservabilityStack`, `AgentWorkflowStack`, `IngestionStack`, `RateLimitStack`, DynamoDB operations math, and metric identity alignments. |
| **Evaluation Runner Gate** | `python scripts/run_evals.py --mode local_pgvector_fixture --persist` | **Security Gate: PASS**, Advisory Quality: WARN | 50 golden cases + 16 adversarial cases; 0 leaks, 0 unsafe responses. Cost: None. |
| **Python Linting (Ruff)** | `python -m ruff check backend` and `infrastructure` (changed files) | **All checks passed!** | Zero errors, zero warnings. |
| **Python Typing (mypy)** | `cd backend && python -m mypy app tests/security` | **Success: no issues found in 165 source files** | Strict typing clean across all modules. |
| **Infrastructure Typing (mypy)** | `cd infrastructure && python -m mypy stacks tests` | **Success: no issues found in 15 source files** | Strict typing clean across all infrastructure files. |
| **Git Diff Whitespace Check** | `git diff --check` | **Clean** | No whitespace, indentation, or line-ending errors. |

---

## 5. Deployment Prerequisites & Operating Notes

1. **Aurora Cluster & Secrets ARNs:**
   - Production and staging CDK deployments require real `aurora_cluster_arn` and `aurora_secret_arn` (enforced by stack assertions).
2. **Bedrock Model Access:**
   - Ensure the AWS target account has model invocation permissions enabled in Bedrock for the configured model IDs:
     - `anthropic.claude-haiku-4-5-20251001-v1:0` (Chat / Research / Risk)
     - `amazon.nova-pro-v1:0` (Reviewer / Judge)
     - `amazon.titan-embed-text-v2:0` (Embeddings)
3. **Alarm Notification Routing:**
   - Add SNS topic actions to `ObservabilityStack.alarms` in deployment pipeline to route alerts to PagerDuty or Slack.
4. **Phase 9 Nightly Evaluation Workflow Limitation:**
   - As documented in Phase 9, GitHub `workflow_dispatch` requires workflow YAML files to be registered on the repository's default branch (`main`). The workflow is ready in `.github/workflows/nightly-eval.yml` and runs locally via `scripts/run_evals.py`.
