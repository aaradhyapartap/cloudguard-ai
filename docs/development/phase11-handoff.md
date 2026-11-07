# Phase 11 — DevOps Handoff

> **Historical Pre-Commit Checkpoint Notice:** This document records the pre-commit review checkpoint for Phase 11. Phase 11 was subsequently committed (`f267aa028df8688dbaac452be4dd0ff3aba928f9` + `PyYAML` follow-up `a318c0c45c70db857dc727061c66ad701b164d5e`) and verified in GitHub Actions CI (Run `42349837636`) on branch `phase-11-devops` (Draft PR #4). For current repository status, see [`current-task.md`](current-task.md) and [`phase12-handoff.md`](phase12-handoff.md).

**Branch:** `phase-11-devops`  
**Parent commit:** `4d7aa9c5b0f68d1858d757484e4df2f7057120e1` (HEAD of `phase-10-observability`)  
**Status:** Locally validated, reviewable checkpoint. Zero cloud resources deployed; zero live costs incurred; working tree unstaged.

---

## 1. Executive Summary

Phase 11 (DevOps) delivers the infrastructure, security federation, deployment pipeline, and vector storage integration required by the CloudGuard AI architecture roadmap, incorporating all review corrections:
1. **GitHub Actions Keyless OIDC Deployment:** Implemented `GitHubOIDCStack` providing OpenID Connect federation (`sts:AssumeRoleWithWebIdentity`) strictly scoped to the repository and exact deployment environment (`repo:aaradhyapartap/cloudguard-ai:environment:{environment_name}`) and audience (`sts.amazonaws.com`). Bootstrap role access is scoped to explicit qualifier `hnb659fds` without wildcard `*`. Data-plane vector actions (`PutVectors`, `QueryVectors`, `DeleteVectors`) are denied to the deployment role, and resource-capable read actions are scoped to `cloudguard-*`.
2. **Protected Manual Deployment Pipeline:** Hardened `.github/workflows/deploy.yml` with explicit manual dispatch (`workflow_dispatch`), preflight CI verification on the exact deployment commit SHA, explicit stack name allowlist validation, safely quoted argument arrays, environment/action validation, Aurora ARN and production Bedrock Guardrail validation, and an automated out-of-VPC Lambda architectural assertion.
3. **Fail-Closed CI Verification & Provenance:** Hardened `scripts/verify_deployment_ci.py` to fail closed when trusted workflow lookup fails, returns malformed data, or finds no matching run, removing generic check-run fallback entirely. Verified selected run identity, repository, path (`.github/workflows/ci.yml`), and head SHA before checking jobs; strictly rejected missing provenance on check runs; prohibited overwriting conflicting API identity fields; and failed immediately on any pagination or API error.
4. **Amazon S3 Vectors Adapter:** Implemented `S3VectorStore` implementing the `VectorStore` port contract with deterministic key layout (`{org_id}#{doc_id}#{chunk_id}`), metadata filtering, batch upsert/query/deletion, cosine similarity conversion, index requirements validation (1024 dims, cosine metric, reading actual SDK field `index.metadataConfiguration.nonFilterableMetadataKeys`), contradictory ID suppression, nonfinite distance rejection, and multi-tenant defense-in-depth isolation checks.
5. **Vector Store Benchmark Tooling:** Created `scripts/benchmark_vector_stores.py` supporting offline zero-cost synthetic fixtures (with true cosine similarity and filter evaluation), local PostgreSQL `local_pgvector` execution (with dynamic per-run unique IDs, pre-insert collision checks, committed-ownership tracking, and scoped cleanup), simulation disclaimers, unmeasured costs reported as null, and gated live-cloud execution requiring an explicit `--confirm-live-benchmark` flag.
6. **Architectural Boundary Inventory:** Documented the boundary between out-of-VPC serverless workers (RDS Data API) and direct-TCP FastAPI (`asyncpg`), strictly forbidding unadapted deployment of the HTTP API into out-of-VPC Lambdas.

---

## 2. Changed & Added File Inventory

| Path | Change Type | Description |
|---|---|---|
| `.github/workflows/deploy.yml` | New | Protected manual deployment workflow via GitHub Actions OIDC federation (`workflow_dispatch`) with environment-scoped variable validation and commit CI gate. |
| `backend/app/adapters/aws/s3_vector_store.py` | New | Amazon S3 Vectors implementation of `VectorStore` port with tenant isolation, clearance filtering, SDK-shaped contract validation reading `index.metadataConfiguration.nonFilterableMetadataKeys`, strict type checks, rejection of non-filterable partition keys, and safe deletion response handling. |
| `backend/tests/unit/test_s3_vector_store.py` | New | Unit tests for S3 Vectors adapter (20 tests covering SDK `response["index"]` schema contract validation, negative controls for non-list/non-string `nonFilterableMetadataKeys`, individual conflict tests for `organization_id`, `confidentiality_level`, `document_id`, positive control for valid content keys, pre-operation execution, synthesized worker environments, upsert, search, suppression, delete error handling). |
| `backend/tests/unit/test_verify_deployment_ci.py` | New | 33 mocked regressions for deployment CI verification covering strict provenance rejection, spoofing/workflow rejection, deterministic reruns, and mocked gh/subprocess regressions for HTTP 403, malformed responses, missing provenance, wrong SHA/workflow/repo, conflicting job identities, pagination failure, and realistic successful workflow runs. |
| `backend/tests/unit/test_benchmark_vector_stores.py` | New | 11 unit tests for vector store benchmark script (mode/store validation, per-run unique IDs, commit failure handling, existing-ID collision abort, concurrent runs isolation, preservation of unrelated rows). |
| `infrastructure/stacks/oidc_stack.py` | New | `GitHubOIDCStack` defining GitHub OIDC Provider and least-privilege deployment IAM role. |
| `infrastructure/tests/test_oidc_stack.py` | New | CDK unit tests asserting OIDC provider, exact environment trust condition, scoped bootstrap roles, and S3 Vectors grants (including `s3vectors:GetIndex`). |
| `infrastructure/tests/test_deploy_workflow.py` | New | 3 tests verifying `.github/workflows/deploy.yml` structure, preflight repo-scoping without environment leak, and `cdk-action` environment variable validation matrix. |
| `scripts/verify_deployment_ci.py` | New | CI check-runs verification script for deployment preflight enforcing that trusted workflow lookup fails closed on HTTP 403 / malformed data / no matching run without generic fallback, verifies selected run identity, repo, path (`.github/workflows/ci.yml`), and head SHA, strictly rejects missing provenance on check runs, prevents overwriting conflicting API identity fields, fails immediately on any pagination/API error, and requires all four successful jobs (`Backend`, `Infrastructure`, `Frontend`, `Security`). |
| `scripts/benchmark_vector_stores.py` | New | Opt-in vector benchmark CLI comparing pgvector and S3 Vectors on latency, recall@k, and cost, with per-run unique IDs, pre-insert collision checks, committed-ownership tracking, and scoped cleanup. |
| `.env.example` | Modified | Documented `AWS_S3_VECTOR_INDEX_ARN`, bucket, index, and guardrail settings. |
| `backend/app/core/config.py` | Modified | Added S3 vector bucket/index/arn settings to `AWSSettings` and `s3_vectors` option to worker settings. |
| `backend/app/core/container.py` | Modified | Wired `S3VectorStore` instantiation with ARN/bucket/index support in `_build_vector_store` for `vector_store="s3_vectors"`. |
| `backend/tests/unit/test_container.py` | Modified | Updated container tests to verify S3 Vectors binding and required configuration. |
| `infrastructure/app.py` | Modified | Wired `GitHubOIDCStack`, aligned environment context key, added S3 vector settings with valid ARN format (`bucket/<bucket>/index/<index>`), and propagated Bedrock Guardrail. |
| `infrastructure/stacks/agent_workflow_stack.py` | Modified | Added optional `s3_vector_index_arn`, `guardrail_id`, `guardrail_version` props; granted Research Agent S3 vectors access (including `s3vectors:GetIndex`) and attached `bedrock:ApplyGuardrail`. |
| `infrastructure/stacks/ingestion_stack.py` | Modified | Added optional `s3_vector_index_arn` prop and granted Document Processor `s3vectors:GetIndex`/`PutVectors`/`GetVectors`/`ListVectors`/`DeleteVectors`. |
| `infrastructure/tests/test_agent_workflow_stack.py` | Modified | Added test asserting synthesized worker environments contain `BEDROCK_GUARDRAIL_ID`/`VERSION` and `bedrock:ApplyGuardrail` policy. |
| `docs/development/current-task.md` | Modified | Updated task tracking to record Phase 11 deliverables and validation. |
| `docs/development/phase11-handoff.md` | New | Comprehensive handoff document and deployment prerequisites inventory. |

---

## 3. Verification Commands & Passing Results

### 3.1 Backend Test Suite (PostgreSQL + RLS)
- **Command:**
  ```powershell
  $env:DB_USER="cloudguard_app"; $env:DB_PASSWORD="cloudguard_app"; $env:RUN_DB_TESTS="1"; $env:REQUIRE_SECURITY_DB_TESTS="1"; python -m pytest backend -ra
  ```
- **Result:** **1,392 passed, 0 failed, 1 warning in 49.60s** (real PostgreSQL 16 + pgvector under `cloudguard_app` with forced RLS).
- **New / Focused Unit Tests:**
  - 33 unit tests in `backend/tests/unit/test_verify_deployment_ci.py` passed in 0.06s.
  - 20 unit tests in `backend/tests/unit/test_s3_vector_store.py` passed in 0.84s.
  - 11 unit tests in `backend/tests/unit/test_benchmark_vector_stores.py` passed in 2.62s.
  - 11 unit tests in `backend/tests/unit/test_container.py` passed in 0.91s.
- **Security Regressions:** `test_application_logger_call_sites_forbid_raw_sensitive_keywords` and `test_application_logs_do_not_render_sensitive_fields_or_tracebacks` passed cleanly.

### 3.2 Infrastructure CDK Test Suite
- **Command:**
  ```powershell
  cd infrastructure && python -m pytest -ra
  ```
- **Result:** **63 passed, 0 failed in 278.59s** (includes 7 OIDC, 3 deploy workflow scoping, negative trust, scoped IAM, S3 Vectors grants with `GetIndex`, and Bedrock Guardrail tests).

### 3.3 Offline CDK Synthesis
- **Command:**
  ```powershell
  cd infrastructure && npx cdk synth -c offline_synth=true --app "python app.py"
  ```
- **Result:** Successfully synthesized all 6 stacks:
  - `CloudGuardIdentity-dev`
  - `CloudGuardRateLimits-dev`
  - `CloudGuardIngestion-dev`
  - `CloudGuardAgentWorkflow-dev`
  - `CloudGuardObservability-dev`
  - `CloudGuardOIDC-dev`

### 3.4 Vector Store Benchmark CLI
- **Synthetic Fixture Mode:**
  ```powershell
  python scripts/benchmark_vector_stores.py --mode synthetic_fixture --num-queries 5
  ```
  *Result:* Successfully completed with zero cloud calls and zero cost. Relative Recall@k: 100.00%.
- **Local pgvector Mode:**
  ```powershell
  $env:RUN_DB_TESTS="1"; $env:DB_USER="cloudguard_app"; $env:DB_PASSWORD="cloudguard_app"; python scripts/benchmark_vector_stores.py --mode local_pgvector --stores pgvector --num-queries 5
  ```
  *Result:* Creates isolated owned `Document` and `DocumentChunk` records, executes `upsert()`, verifies written counts and expected result IDs, and cleans up only records created by the run.
- **Live Cloud Protection Gate:**
  ```powershell
  python scripts/benchmark_vector_stores.py --mode live_cloud
  ```
  *Result:* Exited with code 1, blocked execution without `--confirm-live-benchmark`.

### 3.5 Type Checking & Linting
- `cd backend && python -m mypy app tests/security ../scripts/benchmark_vector_stores.py ../scripts/verify_deployment_ci.py`: **Success: no issues found in 168 source files**.
- `cd infrastructure && python -m mypy stacks tests`: **Success: no issues found in 18 source files**.
- `python -m ruff check backend/app/core/config.py backend/app/core/container.py backend/tests/unit/test_container.py infrastructure/app.py infrastructure/stacks/agent_workflow_stack.py infrastructure/stacks/ingestion_stack.py infrastructure/stacks/oidc_stack.py infrastructure/tests/test_agent_workflow_stack.py infrastructure/tests/test_oidc_stack.py infrastructure/tests/test_deploy_workflow.py backend/app/adapters/aws/s3_vector_store.py backend/tests/unit/test_s3_vector_store.py backend/tests/unit/test_benchmark_vector_stores.py backend/tests/unit/test_verify_deployment_ci.py scripts/benchmark_vector_stores.py scripts/verify_deployment_ci.py`: **All checks passed! Clean: 0 errors**.
- `git diff --check`: **Clean: zero whitespace or line-ending anomalies**.
- Alembic migrations `0001`–`0013`: **Completely unchanged**.

---

## 4. Concrete Review Findings Addressed

### 4.1 Benchmark Ownership & Cleanup Safety (`scripts/benchmark_vector_stores.py` & `test_benchmark_vector_stores.py`)
- **Per-Run Unique Identifiers:** Replaced deterministic fixed chunk/document IDs with dynamic per-run unique UUIDs for documents and chunks.
- **Pre-Insert Collision Check:** Checks database for existing `Document.id` and `DocumentChunk.id` before insertion; aborts immediately without touching existing rows on collision.
- **Committed-Ownership Tracking:** `committed_docs` and `committed_chunks` are tracked strictly after `session.commit()` succeeds, not upon attempted add.
- **Restricted Search:** Restricts `store.search()` strictly to `document_ids=committed_docs`, preventing benchmark queries from evaluating foreign or unrelated records.
- **Safe Cleanup:** Cleanup executes strictly for `DocumentChunk.id.in_(committed_chunks)` and `Document.id.in_(committed_docs)`. On commit failure or collision abort, committed lists are empty so zero delete statements are executed.
- **11 Unit Regressions:** Added regressions in `test_benchmark_vector_stores.py` covering commit failure, ID collision abort, concurrent runs isolation, and preservation of unrelated rows.

### 4.2 Deployment Configuration Preflight (`.github/workflows/deploy.yml` & `infrastructure/tests/test_deploy_workflow.py`)
- **Duplicate Preflight Removal:** Removed duplicate environment-variable validation from the environment-less `preflight-safety` job.
- **Environment-Scoped Job Validation:** Validation remains inside the selected `cdk-action` job with `environment: ${{ github.event.inputs.environment }}` strictly before AWS credential assumption.
- **Workflow Tests:** Added 3 tests in `infrastructure/tests/test_deploy_workflow.py` asserting repo-scoped preflight, pre-credential execution order in `cdk-action`, and environment-scoped validation matrix.

### 4.3 S3 Vectors Index Contract (`backend/app/adapters/aws/s3_vector_store.py` & `test_s3_vector_store.py`)
- **SDK Schema Contract:** Parses the actual AWS SDK `GetIndex` response shape `response["index"]` (not flat dict).
- **Contract Enforcement:** Validates `dimension == 1024`, `distanceMetric.lower() == "cosine"`, and inspects actual SDK field `index.metadataConfiguration.nonFilterableMetadataKeys`.
- **Conflict Rejection:** Rejects any non-filterable `organization_id`, `confidentiality_level`, or `document_id`.
- **Type Validation:** Validates that `metadataConfiguration` is a dict, `nonFilterableMetadataKeys` is a list, and each element is a string. Permits valid non-filterable content keys (e.g. `["content", "section"]`).
- **Pre-Operation Execution:** Validation automatically runs before data operations (`upsert`, `search`, `delete_by_document`).
- **Scoped IAM Permissions:** Granted `"s3vectors:GetIndex"` alongside data actions in `IngestionStack` and `AgentWorkflowStack`.
- **Negative & Positive Controls:** 20 unit tests in `test_s3_vector_store.py` proving non-list/non-string items fail, each conflicting key fails individually, valid non-filterable content keys pass, and 512 dimensions/euclidean metric fail closed.

### 4.4 Trusted CI Provenance (`scripts/verify_deployment_ci.py` & `test_verify_deployment_ci.py`)
- **Fail Closed on Lookup:** Queries `repos/{repo}/actions/workflows/ci.yml/runs?head_sha={commit_sha}&per_page=10` and fails closed on HTTP 403, network failure, malformed JSON, or empty workflow runs.
- **Generic Fallback Removed:** Removed the generic commit check-runs fallback entirely.
- **Run Identity & Provenance Verification:** Verifies the selected workflow run's `id` (int > 0), `head_sha == commit_sha`, `path == ".github/workflows/ci.yml"`, and `repository == repo` before inspecting jobs.
- **Strict Provenance Enforcement:** Rejects missing provenance on check runs (`app`, `workflow_path`, `head_sha`, `run_id`, `repository`) rather than permitting fixture bypasses.
- **No Identity Overwrites:** Never overwrites conflicting API response identity fields with expected values; raises `RuntimeError` if a job's `run_id`, `head_sha`, `workflow_path`, or `app.slug` conflicts with expected values.
- **Pagination Safety:** Fails immediately with `RuntimeError` on any pagination or API error; never accepts partial retrieval.
- **Suite Integrity:** Verifies that Backend, Infrastructure, Frontend, and Security all belong to that exact trusted run, complete, and succeed.
- **33 Unit Regressions:** Mocked unit tests in `test_verify_deployment_ci.py` covering lookup HTTP 403, malformed responses, missing provenance, wrong SHA/workflow/repo, conflicting job identities, pagination failure, and a realistic successful response.

---

## 5. Comprehensive Deployment Prerequisites Inventory

Before executing any live AWS deployment via `.github/workflows/deploy.yml` or manual CDK deploy, the following prerequisites must be provisioned and configured:

### 5.1 AWS Account CDK v2 Bootstrap
- Target AWS account and region must be bootstrapped:
  ```bash
  cdk bootstrap aws://<ACCOUNT_ID>/<REGION>
  ```
- This creates the required CDK v2 execution roles:
  - `arn:aws:iam::<ACCOUNT_ID>:role/cdk-hnb659fds-lookup-role-<ACCOUNT_ID>-<REGION>`
  - `arn:aws:iam::<ACCOUNT_ID>:role/cdk-hnb659fds-deploy-role-<ACCOUNT_ID>-<REGION>`
  - `arn:aws:iam::<ACCOUNT_ID>:role/cdk-hnb659fds-file-publishing-role-<ACCOUNT_ID>-<REGION>`
  - `arn:aws:iam::<ACCOUNT_ID>:role/cdk-hnb659fds-image-publishing-role-<ACCOUNT_ID>-<REGION>`

### 5.2 RDS Aurora Serverless v2 PostgreSQL
- Running Aurora PostgreSQL 16 cluster with pgvector extension.
- **RDS Data API enabled** (required for out-of-VPC Lambda communication).
- Secrets Manager secret containing database master credentials.
- Schema initialized by running migrations `0001`–`0013` and creating the application role `cloudguard_app` with `NOBYPASSRLS`.
- Supply parameters via context or environment:
  - `-c aurora_cluster_arn=arn:aws:rds:<region>:<account>:cluster:<name>`
  - `-c aurora_secret_arn=arn:aws:secretsmanager:<region>:<account>:secret:<name>`

### 5.3 Amazon Bedrock Model Access & Guardrails
- Ensure model access is granted in the deployment region:
  - Claude Haiku 4.5 (`anthropic.claude-haiku-4-5-20251001-v1:0`)
  - Amazon Nova Pro (`amazon.nova-pro-v1:0`)
  - Titan Text Embeddings v2 (`amazon.titan-embed-text-v2:0`)
- If deploying to production (`prod`), Bedrock Guardrails are strictly required:
  - Export `BEDROCK_GUARDRAIL_ID=<id>`
  - Export `BEDROCK_GUARDRAIL_VERSION=<version>`

### 5.4 Amazon S3 Vectors Bucket & Index (Optional / Opt-In)
- If leveraging Amazon S3 Vectors alongside or in place of pgvector:
  - Create S3 Vector bucket and provision index with dimension 1024 and cosine distance.
  - Provide context parameters:
    - `-c s3_vector_index_name=<name>`
    - `-c s3_vector_index_arn=arn:aws:s3vectors:<region>:<account>:bucket/<bucket>/index/<index>`

### 5.5 GitHub Actions OIDC Configuration
- Deploy `CloudGuardOIDC-<env>` stack first or configure GitHub OIDC Provider.
- In GitHub repository settings (under Settings > Environments for `dev`, `staging`, `prod`):
  - Set `AWS_DEPLOY_ROLE_ARN`: `arn:aws:iam::<ACCOUNT_ID>:role/cloudguard-github-deploy-<env>`
  - Set `AWS_REGION`: target region (e.g., `us-east-1`).
  - Set `AWS_AURORA_CLUSTER_ARN`: `arn:aws:rds:<region>:<account>:cluster:<name>`
  - Set `AWS_AURORA_SECRET_ARN`: `arn:aws:secretsmanager:<region>:<account>:secret:<name>`
  - (For `prod`): set `BEDROCK_GUARDRAIL_ID` and `BEDROCK_GUARDRAIL_VERSION`.

### 5.6 Architectural Boundary: Out-of-VPC Lambda / Data API Boundary
> [!IMPORTANT]
> **FastAPI HTTP Application Deployment Restriction**
>
> 1. **Worker Architecture (Implemented & Verified):**
>    - All deployed background workers (Document Processing, Research Agent, Risk Agent, Reviewer Agent, Approval Workers) execute as out-of-VPC Lambda container functions (ADR-0008).
>    - They do not attach to a VPC, eliminating the ~$32/month NAT Gateway.
>    - They interact with Aurora Serverless v2 exclusively through the AWS RDS Data API HTTP endpoint via IAM credentials.
>
> 2. **FastAPI HTTP Layer (Current Constraint):**
>    - The web application routes (`app/api/v1/`) and database dependency (`tenant_session()`) currently rely on SQLAlchemy `AsyncSession` using `asyncpg` over a direct TCP socket connection on port 5432.
>    - Because an out-of-VPC Lambda cannot open direct TCP connections into a private VPC without a VPC interface or public database endpoint, **deploying the existing `asyncpg`-backed FastAPI container into an out-of-VPC Lambda is prohibited**.
>
> 3. **Required Boundary Implementation (Future Scope):**
>    - To deploy the HTTP API without introducing a NAT Gateway, one of two architectural patterns must be implemented:
>      - **Option A (Data API Persistence Boundary):** Implement an RDS Data API persistence adapter for FastAPI web endpoints (or an async Data API engine) matching the worker pattern.
>      - **Option B (In-VPC Deployment):** Deploy the API container to an in-VPC serverless target (e.g., AWS App Runner with VPC connector, ECS Fargate in private subnets with VPC endpoints, or Lambda inside VPC subnets with VPC endpoints).

---

## 6. Incomplete Work & Remaining Blockers

1. **Live Deployment:** No live AWS resources were created or modified during Phase 11. Live deployment must be separately triggered via the manual `.github/workflows/deploy.yml` workflow after GitHub repository variables are configured.
2. **Live Benchmarks:** Live AWS S3 Vectors benchmarking was not executed; only synthetic zero-cost fixture validation and local PostgreSQL benchmarks were run.
3. **Stacked PR Integrity:** Unmerged draft PRs #1, #2, and #3 remain completely preserved.

---

## 7. Review Archive

- **Archive Path:** `D:\cloudguard-ai\cloudguard-phase11-complete-review.zip`
- **File Count:** 381 files
- **Byte Size:** Reported in review summary (~0.80 MB)
- **SHA256:** Generated and reported in review summary
- Complete source archive containing tracked and non-ignored untracked source, generated outside the git repository to facilitate source review before staging or committing.
