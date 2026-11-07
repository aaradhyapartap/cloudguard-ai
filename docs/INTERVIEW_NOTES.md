# CloudGuard AI — Evidence-Backed Interview Notes & Resume Bullets

This document provides **evidence-backed resume bullets** and **technical interview Q&A notes** grounded strictly in the implemented CloudGuard AI codebase, test suites, Architecture Decision Records ([`docs/adr/`](adr/README.md)), and threat model ([`docs/THREAT_MODEL.md`](THREAT_MODEL.md)).

> **Strict Honesty & Provenance Rule:**
> - **Do not** claim production adoption, customer deployments, or unmeasured dollar/time savings.
> - **Do not** present deterministic or local-`pgvector` fixture evaluation metrics (`eval:feature-hash-1024-v1` / `eval:fixture-llm-v1`) as live Amazon Bedrock foundation model quality measurements.
> - **Do not** claim that the AWS CDK stacks have been live-deployed until `.github/workflows/deploy.yml` has been executed against a real AWS account; describe them accurately as **synthesized and assertion-tested CDK stacks**.

---

## 1. Evidence-Backed Resume Bullets

### Variant A — Cloud & AI Systems Engineering (3–4 Bullets)

- **Multi-Tenant RAG & Compliance Platform:** Architected a serverless-ready compliance intelligence platform in Python 3.12 (FastAPI, SQLAlchemy `>=2.1.0`, `pgvector`) and Next.js 15 behind **17 domain port modules** (`18` interfaces: `13` with AWS adapters, `4` local/SQLAlchemy-only on the deferred HTTP API persistence boundary; `boto3` banned outside `app/adapters/`), enforcing four-layer tenant isolation with PostgreSQL `FORCE ROW LEVEL SECURITY` under a `NOBYPASSRLS` runtime role across **1,392 passing backend tests**.
- **Bounded Agentic Workflow & Deterministic Scoring:** Built a fixed `Research → Risk → Reviewer` agent workflow with a fail-closed `ToolRegistry` (intersecting static per-agent allowlists with the caller's JWT `Principal`), deterministic versioned Python compliance scoring (`v1.0`), and Step Functions `.waitForTaskToken` Manager approval pauses backed by trigger-enforced append-only PostgreSQL audit logs ([ADR-0005](adr/0005-deterministic-risk-scoring-in-python.md), [ADR-0017](adr/0017-postgresql-approval-audit-canonical.md)).
- **AI Security & Reproducible Evaluation Harness:** Engineered per-request cryptographic nonce prompt framing, Bedrock Guardrail fail-closed intervention handling, bounded PII minimization, and a reproducible evaluation harness (`50` golden cases + `16` adversarial prompt-injection/boundary cases; **109 blocking security regression tests**) achieving a `100%` adversarial pass rate and `0` cross-tenant or clearance leaks on local `pgvector` fixtures (`precision@5: 0.7064`, `recall@10: 0.9706`, `citation_accuracy: 0.8333`).
- **Keyless IaC, Observability & Vector Store Benchmarking:** Authored **6 AWS CDK (Python) stacks** (**63 CDK tests passing**) covering Cognito PKCE auth, out-of-VPC Aurora Data API workers ([ADR-0008](adr/0008-lambda-outside-vpc-via-aurora-data-api.md)), DynamoDB rate/worker-lease admission, privacy-safe CloudWatch EMF cost/latency telemetry, keyless GitHub OIDC deployment with fail-closed CI provenance checks, and dual `VectorStore` adapters (`pgvector` and Amazon S3 Vectors).

### Variant B — Application & Cloud Security Focus (3 Bullets)

- **Defense-in-Depth Multi-Tenant & RBAC Architecture:** Implemented end-to-end JWT verification (Cognito RS256 JWKS + offline HS256 parity, OAuth2 PKCE `S256` with `state`/`nonce` validation), an exhaustive 3-role (`analyst`, `manager`, `admin`) authorization matrix with role-derived confidentiality clearance (`internal`, `confidential`, `restricted`), and forced PostgreSQL RLS with migration-normalized least-privilege table grants ([`0012_runtime_role_privileges.py`](../backend/migrations/versions/0012_runtime_role_privileges.py)).
- **Hostile-Document & Model-Boundary Hardening:** Treated uploaded documents and LLM outputs as untrusted inputs: enforced per-request nonce context delimiters, application-owned citation mapping (`[S1]`, `[S2]`), unknown identifier/tool rejection, per-document ingestion byte/page/chunk budgets, and DynamoDB conditional `UpdateItem` rate limits and expiring worker concurrency leases (**109 blocking CI security regressions**).
- **Supply-Chain & Deployment Gate Hardening:** Built a 4-job GitHub Actions CI pipeline (`ruff`, `mypy`, PostgreSQL RLS `pytest`, `pip-audit`, `npm audit`, `gitleaks`, `trivy` container scanning) and a manual OIDC deployment workflow (`sts:AssumeRoleWithWebIdentity` scoped to exact repo/environment and CDK qualifier `hnb659fds`) gated by a fail-closed CI provenance verifier (`scripts/verify_deployment_ci.py`, 33 unit tests).

### Variant C — Concise 1-Line Portfolio Summary

- **CloudGuard AI (Python 3.12, FastAPI, PostgreSQL/pgvector, Next.js 15, AWS CDK):** Multi-tenant compliance RAG and bounded `Research → Risk → Reviewer` agent platform featuring PostgreSQL `FORCE RLS`, deterministic Python risk scoring, `.waitForTaskToken` Manager approvals, 6 synthesized CDK stacks, and 1,463 automated tests (1,392 backend + 63 CDK + 8 frontend) including a 50-case golden / 16-case adversarial evaluation harness.

---

## 2. Verifiable Repository Evidence Sheet

Use only these exact, verifiable numbers when discussing the project:

| Metric / Artifact | Exact Value | Source / Verification Command |
|---|---|---|
| **Backend Automated Tests** | `1,392 passed` (`0` failed) | `python -m pytest backend -ra` (with `RUN_DB_TESTS=1` and `REQUIRE_SECURITY_DB_TESTS=1` under `cloudguard_app`) |
| **Blocking Security Regression Suite** | `109 passed` (`94` service/route/contract + `6` callback race + `9` PostgreSQL RLS/catalog) | `python -m pytest backend/tests/security -ra` |
| **Infrastructure CDK Tests** | `63 passed` across `6` synthesized stacks | `cd infrastructure && python -m pytest -ra` |
| **Frontend Vitest Suite** | `8 passed` across `2` test suites (`AuthProvider.test.tsx`, `analytics/page.test.tsx`) | `cd frontend && npm test` |
| **Domain Ports (`app/ports/`)** | `17` port modules (`18` interfaces: `13` with AWS adapters, `4` local/SQLAlchemy-only) | `backend/app/ports/*.py` (Ruff `TID251` bans `boto3` outside `app/adapters/`) |
| **Alembic Schema Revisions** | `0001` through `0013` (`13` immutable forward migrations) | `backend/migrations/versions/` |
| **Golden & Adversarial Evaluation Corpus** | `50` golden cases (`evals/golden_dataset.jsonl`) + `16` adversarial security checks (`8` corpus cases in `evals/adversarial.jsonl` across RAG & agent boundaries) | `python scripts/run_evals.py --mode local_pgvector_fixture` |
| **Fixture Security Gate Outcome** | `PASS` (`0` cross-tenant leaks, `0` unauthorized disclosures, `0` injection breaches, `1.0000` adversarial pass rate) | `docs/development/phase9-handoff.md` |
| **Fixture Quality Gate Outcome** | `WARN` (advisory) — `precision@5: 0.7064`, `recall@10: 0.9706`, `context_relevance: 0.7201`, `groundedness: 1.0000`, `answer_relevance: 0.9633`, `citation_accuracy: 0.8333` (target `0.85`), `unanswerable_abstention_rate: 0.5000` (target `0.80` over 16 ambiguous/unanswerable cases) | Evaluated with deterministic fixture providers (`eval:feature-hash-1024-v1`, `eval:fixture-llm-v1`); **not** live Bedrock models |
| **Fixture Cost per 1,000 Queries** | `None` (`null`) | Explicitly unmeasured in fixture mode (`$0` cloud spend) |

---

## 3. Technical Interview Deep-Dive Q&A

### Q1. Why did you choose RAG over fine-tuning for compliance intelligence? ([ADR-0001](adr/0001-rag-over-fine-tuning.md))
- **Answer:** Compliance corpora change continuously, and three requirements are non-negotiable:
  1. Every claim must cite a specific current document, page, and section.
  2. Retrieval must be filtered at query time by the caller's `organization_id` and role-derived `confidentiality_level`.
  3. Superseded or deleted policies must stop influencing answers immediately.
- Fine-tuning bakes facts into model weights where they cannot be cited, cannot be filtered per user clearance, and cannot be deleted without retraining. RAG keeps knowledge in an indexed store (`pgvector` / `S3VectorStore`) where PostgreSQL RLS and metadata predicates enforce authorization *before* any chunk enters the LLM context.

### Q2. How do you guarantee a bug in application SQL cannot leak another tenant's documents?
- **Answer:** Tenant isolation is enforced across four independent layers:
  1. **Signed JWT → Immutable `Principal`:** `organization_id` is extracted exclusively from the verified token claim (`custom:organization_id`), never from request bodies or headers.
  2. **Explicit Repository Predicates:** Every repository query includes an explicit `WHERE organization_id = :org_id` filter.
  3. **PostgreSQL `FORCE ROW LEVEL SECURITY`:** Every tenant table has `ENABLE` and `FORCE ROW LEVEL SECURITY` with a policy checking `organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid`. `tenant_session()` executes `SET LOCAL app.current_organization_id = ...` inside each transaction.
  4. **Least-Privilege `NOBYPASSRLS` Runtime Role:** The application runs as `cloudguard_app` (`NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`). Migration `0012` revokes blanket privileges and grants only the exact `SELECT`/`INSERT`/`UPDATE` subset each table requires (with zero `DELETE` or `TRUNCATE` grants on application tables).
- **Proof in code:** [`backend/tests/integration/test_tenant_isolation.py`](../backend/tests/integration/test_tenant_isolation.py) and [`backend/tests/security/test_rls_security.py`](../backend/tests/security/test_rls_security.py) execute raw `SELECT * FROM documents` with no `WHERE` clause and verify PostgreSQL returns only the active tenant's rows (and `0` rows if the session variable is unset).
- **Follow-up story ([ADR-0016](adr/0016-no-security-definer-view.md)):** During Phase 2, we initially tried a `SECURITY DEFINER` view to look up users by email during local login before a tenant context existed. It returned `0` rows against real PostgreSQL because `FORCE ROW LEVEL SECURITY` applies even to the table/view owner. Instead of weakening `FORCE RLS` or introducing a risky `BYPASSRLS` role for a dev endpoint, we moved the local dev login roster to an immutable Python fixture (`app/adapters/local/directory.py`) shared with `scripts/seed_data.py`.

### Q3. Why is risk and compliance scoring written in deterministic Python instead of asking the LLM? ([ADR-0005](adr/0005-deterministic-risk-scoring-in-python.md))
- **Answer:** In regulated environments, a compliance score must be reproducible, auditable, versioned (`scoring_version="v1.0"`), and immune to prompt drift or prompt injection.
- **How it works:**
  - The LLM (`ComplianceCandidateExtractionService` / `RiskAgent`) is strictly a **proposer**: it can only suggest candidate control statuses or component estimates paired with retrieved chunk IDs.
  - Application code validates that referenced chunks belong to the tenant and were actually admitted, then runs pure Python math (`app/services/compliance_scoring.py`) over `ControlScoringInput` weights (`1.0`–`5.0`), status values, and grounding penalties (`is_grounded` requires `evidence_count > 0` on satisfied/partially satisfied controls).
  - **Actor-Independent Scoring vs. Clearance-Safe Projection:** Score computation is independent of the caller's clearance ceiling so an Analyst and a Manager computing the same assessment always get the exact same mathematical score. When an Analyst (`internal` ceiling) or a Manager (`confidential` ceiling) views an assessment containing `restricted` evidence above their ceiling, the API projection omits the higher-clearance evidence metadata entirely and sets `hidden_evidence_present = True` without altering the underlying score.

### Q4. How do you defend against indirect prompt injection inside uploaded PDFs? ([`PROMPT_INJECTION_DEFENSES.md`](security/PROMPT_INJECTION_DEFENSES.md))
- **Answer:** We treat prompts as untrusted formatting, **never** as a security boundary. Even if a hostile document completely persuades the LLM, deterministic code blocks escalation at every boundary:
  1. **Per-Request Cryptographic Nonce Framing (`app/security/prompt_framing.py`):** Every generation call generates a fresh unpredictable hex nonce (`secrets.token_hex(16)`) and wraps user queries and retrieved chunks inside `<untrusted_evidence_{nonce}>...</untrusted_evidence_{nonce}>` blocks, stripping or escaping any spoofed delimiter tags inside the document text.
  2. **Fail-Closed `ToolRegistry` (`app/services/tool_registry.py`):** The LLM never receives credentials or callable functions. It emits structured tool-call intent. `ToolRegistry` intersects the agent's static tool allowlist (`ResearchAgent` has read-only `search_documents` / `get_chunk`; `RiskAgent` and `ReviewerAgent` have **zero** tools) with the human caller's `Principal` permissions and a hard per-run call budget.
  3. **Authoritative Source & ID Membership Checks:** `RAGResponse.sources` is constructed from the application's admitted retrieval map (`[S1]`, `[S2]`), never from model-invented UUIDs. Agent outputs referencing unknown chunk IDs, mismatched document IDs, or out-of-scope control IDs fail closed.
  4. **Bedrock Guardrail Intervention Handling (`app/adapters/bedrock/llm.py`):** Production requires `BEDROCK_GUARDRAIL_ID` and `BEDROCK_GUARDRAIL_VERSION`; if Bedrock returns `stopReason == "guardrail_intervened"` or `guardrailAction == "INTERVENED"`, the adapter records telemetry and raises a sanitized `UpstreamError` without returning model text.

### Q5. Why do deployed worker Lambdas run outside the VPC via the Aurora Data API, and what is the HTTP API boundary? ([ADR-0008](adr/0008-lambda-outside-vpc-via-aurora-data-api.md))
- **Answer:** Placing Lambda functions inside private VPC subnets to reach Aurora over TCP port 5432 requires either a NAT Gateway (~$32/month fixed idle floor per AZ — more than all demo AI spend combined) or multiple paid VPC Interface Endpoints (~$7.20/month each) to reach Bedrock, Step Functions, S3 Vectors, and CloudWatch.
- By keeping all 7 background worker Lambdas (`deployed_document_worker.py`, `deployed_research_agent_worker.py`, `deployed_risk_agent_worker.py`, `deployed_reviewer_agent_worker.py`, `deployed_approval_creation_worker.py`, `deployed_approval_callback_recovery_worker.py`, `deployed_approved_action_execution_worker.py`) **outside the VPC**, they reach Bedrock, S3, DynamoDB, and Step Functions directly over AWS public endpoints and query Aurora Serverless v2 over HTTPS via the **RDS Data API** (`rds-data:ExecuteStatement` + IAM/Secrets Manager), setting `app.current_organization_id` inside Data API transactions and casting vectors with `:embedding::vector`.
- **Honest architectural boundary:** The synchronous FastAPI HTTP layer (`app/api/v1/`) and its 4 synchronous persistence ports (`approval_repository`, `compliance_repository`, `evaluation_repository`, `approved_action_executor`) use SQLAlchemy `AsyncSession` (`asyncpg`) over TCP port 5432 for fast local/CI testing. Because an out-of-VPC Lambda cannot open raw TCP connections into a private VPC, `.github/workflows/deploy.yml` and our handoff explicitly prohibit deploying the unadapted `asyncpg` HTTP container into an out-of-VPC Lambda until either an HTTP Data API repository adapter or an in-VPC compute target is added.

### Q6. How does human approval work, and how did you solve the Step Functions / PostgreSQL callback race (`CG-14`)?
- **Answer:**
  - When `ReviewerAgent` passes a high-risk finding, Step Functions invokes the approval creation worker (`deployed_approval_creation_worker.py`) with `.waitForTaskToken`, which persists a `PENDING` approval row and the internal `task_token` in PostgreSQL (the token is never exposed in HTTP responses, public models, audit payloads, or logs).
  - Only a `Manager` holds `Permission.APPROVAL_DECIDE` (`Analyst` and `Admin` receive `403`). The Manager's decision (`approve`, `reject`, or `modify`) commits atomically to PostgreSQL first, firing a database trigger that writes an immutable row to `approval_audit_events` ([ADR-0017](adr/0017-postgresql-approval-audit-canonical.md)).
  - A recovery worker (`deployed_approval_callback_recovery_worker.py`) claims pending callbacks, calls `sfn:SendTaskSuccess`, and then commits `callback_delivered_at` in PostgreSQL.
  - **The Race (`CG-14`):** `SendTaskSuccess` immediately resumes the Step Functions state machine, so the next state (`deployed_approved_action_execution_worker.py`) can query Aurora *milliseconds before* the callback worker's `UPDATE ... SET callback_delivered_at` transaction commits.
  - **The Fix:** We never mark `callback_delivered_at` before `SendTaskSuccess` succeeds (which would lie if the AWS call failed). Instead, `AuroraDataAPIApprovalExecutionRepository.execute_for_workflow` checks `callback_delivered_at` and, if still uncommitted, raises `CallbackDeliveryNotCommittedError`, **explicitly rolls back its Data API transaction to release the `FOR UPDATE` row lock** (failing closed if rollback fails), and performs a bounded readiness poll (4 attempts, 50 ms backoff) before executing the idempotent `create_security_review` side effect.

### Q7. How did you design telemetry and cost tracking without risking request crashes or cardinality explosions? (Phase 10)
- **Answer:**
  1. **Boundary Failure Isolation (`safe_telemetry_call`):** Every telemetry call across middleware, RAG, retrieval, admission, and agents is wrapped in `safe_telemetry_call`, which catches and logs any emitter error without ever crashing user requests, masking upstream exceptions, or skipping `finally` slot-lease cleanup.
  2. **Closed Dimension Sets & Privacy Scrubbing:** Metric dimensions are restricted to 8 allowlisted keys (`Environment`, `Operation`, `ModelId`, `Category`, `Status`, `Stage`, `Severity`, `AdmissionDecision`) with closed value sets. High-cardinality or sensitive identifiers (`organization_id`, `user_id`, `document_id`, `request_id`, prompts, tokens) are forbidden in metric dimensions.
  3. **Model-Call vs. User-Query Cost Separation:** `record_token_usage` tracks per-call usage (`CostMeasuredModelCalls`), while `record_query_cost` tracks user-query cost (`CostMeasuredQueries`, `EstimatedInferenceCostUSD`). If a query mixes a priced model call with an unmeasured/fixture call, or if token counts are incomplete (`None`), the query is recorded as partial/unmeasured (`CostPartialQueries = 1.0`, `CostUnmeasuredQueries = 1.0`) rather than fabricating `$0.00` and corrupting average cost-per-query. In CloudWatch, `ObservabilityStack` graphs `IF(measured > 0, cost / measured)`.

### Q8. What did you deliberately *not* build, and what would you change at 100,000 users?
- **Deliberately not built:**
  - *Emergent multi-agent chat/delegation:* Unpredictable loops cannot be audited in compliance; a fixed Step Functions graph (`Research → Risk → Reviewer`) with static tool allowlists is reconstructable.
  - *Direct PostgreSQL + DynamoDB audit dual writes:* Without a transactional outbox, dual writes diverge on partial failure ([ADR-0017](adr/0017-postgresql-approval-audit-canonical.md)).
  - *Unbounded autonomous remediation:* Consequential actions are restricted to a single schema-validated action (`create_security_review`) gated by a Manager's persisted approval.
- **Where it breaks first at 100k users / 100M vectors:**
  1. **Aurora Data API throughput & HNSW shared buffers:** At high QPS or >10M–100M 1024-dim vectors, HNSW graphs exceed Aurora buffer cache and Data API HTTPS serialization becomes a bottleneck. Move HTTP/worker DB connections to in-VPC RDS Proxy and migrate vector search to OpenSearch Serverless NextGen or partitioned S3 Vectors ([ADR-0002](adr/0002-pgvector-first-s3-vectors-second.md)).
  2. **Static SPA `sessionStorage` token storage:** Introduce a thin BFF (Backend-for-Frontend) issuing `httpOnly`, `Secure`, `SameSite=Strict` session cookies ([ADR-0015](adr/0015-token-storage-and-id-tokens.md)).
  3. **Fixed-window DynamoDB rate counters:** Hot tenant partition keys under extreme burst load would require token-bucket sharding or API Gateway + AWS WAF edge throttling in front of Lambda ([ADR-0018](adr/0018-shared-api-rate-admission.md)).
