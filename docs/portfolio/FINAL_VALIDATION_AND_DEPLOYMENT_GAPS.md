# CloudGuard AI — Final Release Validation & Source-Based AWS Deployment-Gap Assessment

- **Repository:** `D:\cloudguard-ai\cloudguard-ai-phase2\cloudguard-ai`
- **Origin:** `https://github.com/aaradhyapartap/cloudguard-ai.git` (`aaradhyapartap/cloudguard-ai`)
- **Branch:** `main`
- **Verified HEAD Commit:** `10d0e5d331439bc417a188121cd17213b2f05010` (`Merge pull request #5 from aaradhyapartap/phase-12-polish`)
- **Tracked-File Status:** Clean (`git diff HEAD` is empty; only unstaged portfolio deliverables under `docs/portfolio/` are untracked).
- **Assessment Scope:** Local release validation across backend (including an isolated clean-database run), infrastructure, frontend, dependency/security scanners, verification of source identity, and a source-verified AWS deployment-gap assessment. No application code, tests, workflows, dependencies, Dockerfile, scanner exceptions, or migrations were modified, and no cloud resources were deployed.

---

## Part 1: Source Identity Verification & Discrepancy Resolution

### 1.1 Repository & Branch Verification

| Check | Exact Command | Output | Exit Code |
| :--- | :--- | :--- | :--- |
| Current Branch | `git branch --show-current` | `main` | `0` |
| Exact HEAD Commit | `git rev-parse HEAD` | `10d0e5d331439bc417a188121cd17213b2f05010` | `0` |
| Remote Origin | `git remote get-url origin` | `https://github.com/aaradhyapartap/cloudguard-ai.git` | `0` |
| Tracked File Diff vs. HEAD | `git diff HEAD -- infrastructure/requirements.txt .github/workflows/deploy.yml` | *(empty — working tree files match `HEAD` byte-for-byte)* | `0` |

### 1.2 Exact Comparison of `infrastructure/requirements.txt` and `.github/workflows/deploy.yml` Against `git show HEAD`

#### `infrastructure/requirements.txt` (`git show HEAD:infrastructure/requirements.txt` — lines 1–3)
```text
aws-cdk-lib>=2.170.0
constructs>=10.4.0
PyYAML>=6.0.2,<7
```

#### `.github/workflows/deploy.yml` (`git show HEAD:.github/workflows/deploy.yml` — lines 151–155 & 251–267)
```yaml
      - name: Install CDK Dependencies
        run: |
          npm install -g aws-cdk
          pip install -r infrastructure/requirements.txt
```
```yaml
          # Context parameters matching app.py consumption
          CONTEXT_ARGS=(
            "--context" "env=${TARGET_ENV}"
            "--context" "environment=${TARGET_ENV}"
          )
          if [[ -n "${AURORA_CLUSTER_ARN}" ]]; then
            CONTEXT_ARGS+=("--context" "aurora_cluster_arn=${AURORA_CLUSTER_ARN}")
          fi
          if [[ -n "${AURORA_SECRET_ARN}" ]]; then
            CONTEXT_ARGS+=("--context" "aurora_secret_arn=${AURORA_SECRET_ARN}")
          fi
          if [[ -n "${GUARDRAIL_ID}" ]]; then
            CONTEXT_ARGS+=("--context" "bedrock_guardrail_id=${GUARDRAIL_ID}")
          fi
          if [[ -n "${GUARDRAIL_VERSION}" ]]; then
            CONTEXT_ARGS+=("--context" "bedrock_guardrail_version=${GUARDRAIL_VERSION}")
          fi
```

### 1.3 Explanation & Resolution of the Earlier Report's Discrepancy

1. **What the initial draft of this report claimed:**
   - The initial draft erroneously stated that `infrastructure/requirements.txt` specified `aws-cdk-lib>=2.172.0,<3.0.0`, that `.github/workflows/deploy.yml` line 183 installed `npm install -g aws-cdk@2.172.0`, and that `deploy.yml` failed to pass `aurora_cluster_arn` / `aurora_secret_arn` into `cdk diff` / `cdk deploy`.
2. **Why that discrepancy occurred (Command-Log Provenance):**
   - During local CDK synthesis validation in step `6901` (`task-6901`), `cdk` was not installed globally on the Windows host, so the validation command was invoked manually via `npx` with an ad-hoc CLI version argument:
     ```powershell
     npx --yes aws-cdk@2.172.0 synth -c offline_synth=true --app "python app.py" -o cdk.out.validation
     ```
     That ad-hoc CLI invocation exited `1` with:
     `Cloud assembly schema version mismatch: Maximum schema version supported is 38.x.x, but found 54.0.0` (because `pip` had installed `aws-cdk-lib==2.250.0` from `aws-cdk-lib>=2.170.0`).
   - Immediately afterward in `task-6901`, running:
     ```powershell
     npx --yes aws-cdk@latest synth -c offline_synth=true --app "python app.py" -o cdk.out.validation
     ```
     installed `aws-cdk@2.1117.0` and succeeded (`exit code 0`), synthesizing all 6 stacks.
   - Following context compaction, the ad-hoc `aws-cdk@2.172.0` command-line pin from `task-6901` was misattributed to `.github/workflows/deploy.yml`.
3. **Verified Conclusion:**
   - `.github/workflows/deploy.yml` line 153 runs `npm install -g aws-cdk` (unpinned, which installs the latest `aws-cdk` CLI matching `npx --yes aws-cdk@latest`), validates `AWS_AURORA_CLUSTER_ARN`, `AWS_AURORA_SECRET_ARN`, `BEDROCK_GUARDRAIL_ID`, and `BEDROCK_GUARDRAIL_VERSION` at lines 173–191, and passes all four as `--context` arguments at lines 256–267.
   - **There is no CDK CLI schema mismatch defect or missing Aurora/Guardrail context forwarding in `.github/workflows/deploy.yml`.**

---

## Part 2: Final Release Validation Results

### 2.1 Environment & Tool Version Inventory

| Tool / Service | Command | Verified Version / Status | Exit Code |
| :--- | :--- | :--- | :--- |
| Python | `python --version` | `Python 3.14.3` | `0` |
| Ruff | `python -m ruff --version` | `ruff 0.16.4` | `0` |
| Mypy | `python -m mypy --version` | `mypy 2.4.1 (compiled: yes)` | `0` |
| Pytest | `python -m pytest --version` | `pytest 9.1.1` | `0` |
| Node.js / npm | `node --version` / `npm --version` | `v24.14.0` / `11.9.0` | `0` |
| Docker | `docker --version` | `Docker version 29.9.2, build a82e528` | `0` |
| Demo PostgreSQL (`cloudguard-postgres`) | `docker ps` + `SELECT version_num FROM alembic_version` | `pgvector/pgvector:pg16` on `127.0.0.1:5432`, revision `0013` (preserved untouched) | `0` |
| Local Demo Backend | `GET http://127.0.0.1:8000/api/v1/health/ready` | `200 OK` (`DB_USER=cloudguard_app`, `NOBYPASSRLS`, preserved running) | `0` |
| Local Demo Frontend | `GET http://localhost:3000/login/` | `200 OK` (Next.js dev server preserved running) | `0` |

---

### 2.2 Category 1 — Backend Lint (`ruff`) and Typecheck (`mypy`)

| Check | Exact Command | Tool Version | Exit Code | Totals / Result |
| :--- | :--- | :--- | :--- | :--- |
| Backend Ruff | `cd backend && python -m ruff check .` | `ruff 0.16.4` | `0` | `All checks passed!` (`0` errors, `0` warnings) |
| Backend Mypy (CI target) | `cd backend && python -m mypy app tests/security` | `mypy 2.4.1` | `0` | `Success: no issues found in 166 source files` |
| Backend + Scripts Mypy | `cd backend && python -m mypy app tests/security ../scripts/benchmark_vector_stores.py ../scripts/verify_deployment_ci.py` | `mypy 2.4.1` | `0` | `Success: no issues found in 168 source files` |

---

### 2.3 Category 2 — Full Backend & Security Test Suites Under `cloudguard_app` (`NOBYPASSRLS`)

#### A. Initial Run Against Shared Demo Database (`127.0.0.1:5432`)
When run against the preserved demo PostgreSQL container (`cloudguard-postgres` on port `5432`, which contained the manual `DEMO_WALKTHROUGH.md` Step 5 framework row `id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'` in `compliance_frameworks`):
- `python -m pytest tests/security -ra` → **`109 passed, 0 failed, 0 skipped`** (`2.72s`, `exit code 0`)
- `python -m pytest -ra` → **`1,390 passed, 2 failed, 0 skipped`** (`60.92s`, `exit code 1`), where `test_route_protection_matrix[POST-/api/v1/compliance/assessments-analyst]` and `test_route_protection_matrix[POST-/api/v1/compliance/assessments-manager]` in `backend/tests/integration/test_authenticated_api.py` received `201 Created` instead of `404 Not Found` because `MISSING_DOCUMENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"` matched the demo framework row `SOC2-DEMO`.

#### B. Isolated Clean-Database Run (`cloudguard-postgres-isolated-test` on `127.0.0.1:55432`)
To verify the backend test suite in complete isolation without touching the demo database volume (`cloudguard-ai_postgres-data`):
1. **Connection Isolation Verification:**
   - Inspected `backend/app/core/config.py` (lines 31–58, `DatabaseSettings` with `env_prefix="DB_"`), `backend/app/repositories/database.py` (lines 56–72), `backend/migrations/env.py` (line 27), `scripts/seed_data.py` (lines 37–41), and `backend/tests/conftest.py` (lines 117–140, `database_admin_session()`).
   - Verified that **both** application runtime connections (`get_settings().database.async_dsn` / `sync_dsn`) **and** test-admin control-plane connections (`database_admin_session()`) read `DB_HOST`, `DB_PORT`, and `DB_NAME` (with `database_admin_session()` reading `TEST_DB_ADMIN_USER` and `TEST_DB_ADMIN_PASSWORD`).
2. **Disposable Container & CI-Identical Bootstrap:**
   - Started disposable container `cloudguard-postgres-isolated-test` (`pgvector/pgvector:pg16`) bound to `127.0.0.1:55432:5432` backed by an isolated in-memory filesystem (`--tmpfs /var/lib/postgresql/data`).
   - Provisioned `cloudguard_app` (`LOGIN PASSWORD 'cloudguard_app' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS`) and granted `CONNECT ON DATABASE cloudguard` and `USAGE ON SCHEMA public` exactly as in `.github/workflows/ci.yml` lines 51–71.
   - Executed `python -m alembic upgrade head` (`0001` → `0013`, `exit code 0`) and `python ../scripts/seed_data.py` (`Seeded 2 organizations and 4 users (with baseline evaluation runs)`, `0` rows in `compliance_frameworks`, `exit code 0`) against `DB_HOST=127.0.0.1`, `DB_PORT=55432`.
3. **Isolated Pytest Execution & Teardown:**
   - Executed with:
     ```powershell
     $env:DB_HOST="127.0.0.1"
     $env:DB_PORT="55432"
     $env:DB_USER="cloudguard_app"
     $env:DB_PASSWORD="cloudguard_app"
     $env:DB_NAME="cloudguard"
     $env:TEST_DB_ADMIN_USER="cloudguard"
     $env:TEST_DB_ADMIN_PASSWORD="cloudguard"
     $env:RUN_DB_TESTS="1"
     $env:REQUIRE_SECURITY_DB_TESTS="1"
     python -m pytest tests/security -ra
     python -m pytest -ra
     ```
   - Immediately removed the temporary container after test completion:
     ```powershell
     docker rm -f cloudguard-postgres-isolated-test
     ```

| Isolated Suite (`127.0.0.1:55432`) | Exact Command | Tool Version | Exit Code | Totals |
| :--- | :--- | :--- | :--- | :--- |
| Dedicated Security Regressions | `cd backend && python -m pytest tests/security -ra` | `pytest 9.1.1` | `0` | **`109 passed, 0 failed, 0 skipped`** (`1` warning in `1.85s`) |
| Full Backend Suite (Unit + Integration + Security) | `cd backend && python -m pytest -ra` | `pytest 9.1.1` | `0` | **`1,392 passed, 0 failed, 0 skipped`** (`1` warning in `35.00s`) |

- **Explicit Skip Confirmation:** **`0` tests were skipped** across both the `109`-test security suite and the `1,392`-test full backend suite (`RUN_DB_TESTS=1` and `REQUIRE_SECURITY_DB_TESTS=1` active).
- **Temporary Resource Cleanup:** `cloudguard-postgres-isolated-test` (container ID `1afdab3b8955...`, tmpfs storage) was removed (`docker rm -f cloudguard-postgres-isolated-test`, `exit code 0`). Demo container `cloudguard-postgres` (`5432`) and volume `cloudguard-ai_postgres-data` were untouched.

---

### 2.4 Category 3 — Infrastructure Typecheck (`mypy`), Pytest Suite, and Offline CDK Synthesis

| Check | Exact Command | Tool Version | Exit Code | Totals / Result |
| :--- | :--- | :--- | :--- | :--- |
| Infrastructure Mypy | `cd infrastructure && python -m mypy stacks tests` | `mypy 2.4.1` | `0` | `Success: no issues found in 18 source files` |
| Infrastructure Pytest | `cd infrastructure && python -m pytest -ra` | `pytest 9.1.1` | `0` | **`63 passed, 0 failed, 0 skipped`** (`252.13s`) |
| Offline CDK App Synthesis | `cd infrastructure && $env:CDK_OFFLINE_SYNTH="1"; python app.py` | `aws-cdk-lib 2.250.0` | `0` | Synthesized all 6 stacks without AWS credentials |
| Offline CDK CLI Synthesis (`npm install -g aws-cdk` equivalent) | `cd infrastructure && npx --yes aws-cdk@latest synth -c offline_synth=true --app "python app.py" -o cdk.out.validation` | `aws-cdk 2.1117.0` | `0` | Synthesized `CloudGuardIdentity-dev`, `CloudGuardRateLimits-dev`, `CloudGuardIngestion-dev`, `CloudGuardAgentWorkflow-dev`, `CloudGuardObservability-dev`, `CloudGuardOIDC-dev` |

---

### 2.5 Category 4 — Frontend Lint, Typecheck, Vitest, and Production Build

| Check | Exact Command | Tool Version | Exit Code | Totals / Result |
| :--- | :--- | :--- | :--- | :--- |
| Frontend ESLint | `cd frontend && npm run lint` (`eslint src --max-warnings 0`) | `eslint 8.57.1` | `0` | `0` errors, `0` warnings |
| Frontend TypeScript | `cd frontend && npm run typecheck` (`tsc --noEmit`) | `typescript 5.9.3` | `0` | `0` type errors |
| Frontend Unit Tests | `cd frontend && npm test` (`vitest run`) | `vitest 3.2.4` | `0` | **`2 passed` test files, `8 passed` tests (`0` failed, `0` skipped)** (`5.85s`) |
| Frontend Production Build | `cd frontend && npm run build` (`next build`) | `Next.js 15.5.15` | `0` | Static export succeeded (`7/7` pages across `/`, `/_not-found`, `/analytics`, `/dashboard`, `/login`) |

---

### 2.6 Category 5 — Python and Frontend Dependency Audits

| Audit Target | Exact Command | Tool Version | Exit Code | Totals / Result |
| :--- | :--- | :--- | :--- | :--- |
| Backend Python Dependencies (CI sequence) | `python -m pip install --upgrade pip && pip-audit --desc` (in isolated audit venv with `./backend` installed per `.github/workflows/ci.yml` lines 211–214) | `pip-audit 2.10.1` (`pip 26.3`) | `0` | **`No known vulnerabilities found` (`0` vulnerabilities across all backend dependencies)** *(Note: before upgrading `pip` from `25.3` to `26.3` in the scratch venv, `pip-audit` flagged `pip 25.3` itself.)* |
| Infrastructure Python Dependencies | `pip-audit -r infrastructure/requirements.txt --desc` | `pip-audit 2.10.1` | `0` | **`No known vulnerabilities found` (`0` vulnerabilities)** |
| Frontend npm Dependencies | `cd frontend && npm audit --audit-level=high` | `npm 11.9.0` | `0` | **`0` high, `0` critical vulnerabilities** (`6` moderate dev-only advisories in `minimatch` under `@typescript-eslint/*`) |

---

### 2.7 Category 6 — Secret Scanning (`gitleaks`) and Container Vulnerability Scanning (`trivy`)

#### A. Gitleaks Secret Scan
- **Command:**
  ```powershell
  docker run --rm -e GIT_CONFIG_NOSYSTEM=1 -e GIT_CONFIG_GLOBAL=/dev/null -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/work -v "D:/cloudguard-ai/cloudguard-ai-phase2/cloudguard-ai:/work:ro" -w /work zricethezav/gitleaks:v8.24.3@sha256:e1b35e12a8c6fa8901f060459cfb6b2fc4c484d3afbe3b029733a3bbfab07055 detect --source=/work --redact --no-color -v --exit-code=1 --log-opts="HEAD"
  ```
- **Scanner Image:** `zricethezav/gitleaks:v8.24.3@sha256:e1b35e12a8c6fa8901f060459cfb6b2fc4c484d3afbe3b029733a3bbfab07055`
- **Result:** **`59 commits scanned`, `~3,341,119 bytes (3.34 MB) in 26.7s`, `no leaks found` (`exit code 0`)**.

#### B. Container Build & Trivy Vulnerability Scan Details
- **Build Command:** `docker build -t cloudguard-backend:ci backend` (`exit code 0`)
- **Scan Command:**
  ```powershell
  docker run --rm -v //var/run/docker.sock:/var/run/docker.sock -v "D:/cloudguard-ai/cloudguard-ai-phase2/cloudguard-ai:/work:ro" -w /work aquasec/trivy:0.69.3 image --format table --exit-code 1 --pkg-types os,library --scanners vuln --severity CRITICAL,HIGH --ignorefile /work/.trivyignore.yaml cloudguard-backend:ci
  ```
- **Scanned Image Metadata:**
  - **Image Tag:** `cloudguard-backend:ci`
  - **Image ID / Manifest List Digest:** `sha256:91fe1977598fe356aa73bdefbd74f39cae5c4f85dcb8f95333792eaa9922c770`
  - **Platform Manifest Digest:** `sha256:8388f61a0b55a6cbfed6486d5b024743ad2c0bc6b3664b4f53d768b969d56468`
  - **Image Config Digest:** `sha256:c2e357121b6d99ffc079744a508207b6915e6865f0471d570f15655d49ee206b`
  - **Base Image Resolved by BuildKit (`backend/Dockerfile` line 7 `FROM public.ecr.aws/lambda/python:3.12`):**
    `public.ecr.aws/lambda/python:3.12@sha256:7236d211c350c63b264efb59f4a257a34acc4a28454f556488981918bb96b394`
  - **Detected Base OS:** `amazon 2023.12.20261026 (Amazon Linux)` (`88` OS packages)
  - **Scanner Version:** `aquasec/trivy:0.69.3` (image ID `bcc376de8d77`)
  - **Suppressed Finding (via `.trivyignore.yaml`):** `CVE-2026-111943` on `usr/local/bin/aws-lambda-rie` (`expired_at: 2026-12-01`)
  - **Python Packages (`39` packages in `/var/lang/lib/python3.12/site-packages`) & `usr/local/bin/aws-lambda-rie`:** **`0` unignored vulnerabilities**
  - **Base OS Findings (`exit code 1` — `Total: 10 (HIGH: 10, CRITICAL: 0)`):**

| Library (OS RPM) | Vulnerability ID | Severity | Status | Installed Version | Fixed Version | Title |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `glibc` | `CVE-2026-86805` | `HIGH` | `fixed` | `2.34-231.amzn2023.0.5` | `2.34-231.amzn2023.0.6` | glibc: Privilege escalation and arbitrary code execution via TOCTOU race condition |
| `glibc-common` | `CVE-2026-86805` | `HIGH` | `fixed` | `2.34-231.amzn2023.0.5` | `2.34-231.amzn2023.0.6` | glibc: Privilege escalation and arbitrary code execution via TOCTOU race condition |
| `glibc-langpack-en` | `CVE-2026-86805` | `HIGH` | `fixed` | `2.34-231.amzn2023.0.5` | `2.34-231.amzn2023.0.6` | glibc: Privilege escalation and arbitrary code execution via TOCTOU race condition |
| `glibc-minimal-langpack` | `CVE-2026-86805` | `HIGH` | `fixed` | `2.34-231.amzn2023.0.5` | `2.34-231.amzn2023.0.6` | glibc: Privilege escalation and arbitrary code execution via TOCTOU race condition |
| `rpm` | `CVE-2026-103242` | `HIGH` | `fixed` | `4.16.1.3-29.amzn2023.0.8` | `4.16.1.3-29.amzn2023.0.9` | rpm: Heap-based buffer overflow write in `hex2binv()` via a mistyped `RPMTAG_FILESIGNATURES` header |
| `rpm` | `CVE-2026-84837` | `HIGH` | `fixed` | `4.16.1.3-29.amzn2023.0.8` | `4.16.1.3-29.amzn2023.0.9` | rpm: Command Injection in `rpmbuild -t*` (`getTarSpec`) via Unescaped Tarball Path |
| `rpm` | `CVE-2026-95519` | `HIGH` | `fixed` | `4.16.1.3-29.amzn2023.0.8` | `4.16.1.3-29.amzn2023.0.9` | rpm: Code Execution via Macro Expansion of Manifest Entries in `rpmgi` |
| `rpm-libs` | `CVE-2026-103242` | `HIGH` | `fixed` | `4.16.1.3-29.amzn2023.0.8` | `4.16.1.3-29.amzn2023.0.9` | rpm: Heap-based buffer overflow write in `hex2binv()` via a mistyped `RPMTAG_FILESIGNATURES` header |
| `rpm-libs` | `CVE-2026-84837` | `HIGH` | `fixed` | `4.16.1.3-29.amzn2023.0.8` | `4.16.1.3-29.amzn2023.0.9` | rpm: Command Injection in `rpmbuild -t*` (`getTarSpec`) via Unescaped Tarball Path |
| `rpm-libs` | `CVE-2026-95519` | `HIGH` | `fixed` | `4.16.1.3-29.amzn2023.0.8` | `4.16.1.3-29.amzn2023.0.9` | rpm: Code Execution via Macro Expansion of Manifest Entries in `rpmgi` |

---

### 2.8 Category 7 — Whitespace & Diff Formatting Check

| Check | Exact Command | Exit Code | Output |
| :--- | :--- | :--- | :--- |
| Git Diff Check | `git diff --check` | `0` | Clean (`0` whitespace or conflict-marker issues) |

---

## Part 3: Source-Verified AWS Deployment-Gap Assessment

### 3.1 Confirmed Defects in Current Repository Source (Backed by Exact File Excerpts)

#### Confirmed Defect 1 — `deployed_approval_callback_recovery_worker.py` and `deployed_approved_action_execution_worker.py` Call `get_settings()` Instead of `get_agent_worker_settings()`

- **Files & Lines:**
  - `backend/app/deployed_approval_callback_recovery_worker.py` (lines 15, 24–25, 44–45):
    ```python
    from app.core.config import get_settings
    ...
    def _build_repository() -> AuroraDataAPIApprovalCallbackRecoveryRepository:
        settings = get_settings()
    ...
    def _build_callback() -> StepFunctionsApprovalCallback:
        settings = get_settings()
    ```
  - `backend/app/deployed_approved_action_execution_worker.py` (lines 14, 31–32, 61):
    ```python
    from app.core.config import get_settings
    ...
    def _build_repository() -> AuroraDataAPIApprovalExecutionRepository:
        settings = get_settings()
    ...
    async def _handle(event: dict[str, Any]) -> dict[str, Any]:
        ...
        settings = get_settings()
    ```
  - `infrastructure/stacks/agent_workflow_stack.py` (lines 68–76 and 186–218):
    ```python
        common_environment = {
            "ENVIRONMENT": environment_name,
            "LLM_PROVIDER": "bedrock",
            "ENABLE_AGENTIC_WORKFLOWS": feature_flag,
        }
        ...
        # ApprovalCallbackRecoveryFunction & ApprovedActionExecutionFunction environment:
                environment={
                    **common_environment,
                    "AWS_AURORA_CLUSTER_ARN": aurora_cluster_arn,
                    "AWS_AURORA_SECRET_ARN": aurora_secret_arn,
                    "DB_NAME": "cloudguard",
                },
    ```
  - `backend/app/core/config.py` (lines 198, 266–280, and 344–350):
    ```python
        identity_provider: Literal["local", "cognito"] = "local"
        ...
        @field_validator("identity_provider")
        @classmethod
        def _local_auth_is_local_only(cls, value: str, info: ValidationInfo) -> str:
            if value == "local" and info.data.get("environment") is not Environment.LOCAL:
                raise ValueError(
                    "identity_provider='local' is permitted only when environment='local'. "
                    "Set IDENTITY_PROVIDER=cognito and configure COGNITO_USER_POOL_ID."
                )
            return value
    ```
- **Verified Runtime Consequence:**
  - Unlike `deployed_approval_creation_worker.py` (line 33: `_settings = get_agent_worker_settings()`), `deployed_research_agent_worker.py` (line 34), `deployed_risk_agent_worker.py` (line 33), and `deployed_reviewer_agent_worker.py` (line 35), these two workers call `get_settings()` (`Settings`).
  - In any non-`local` environment (`ENVIRONMENT="dev"` or `"prod"`), `ApprovalCallbackRecoveryFunction` and `ApprovedActionExecutionFunction` do not have `IDENTITY_PROVIDER=cognito`, `COGNITO_USER_POOL_ID`, or `COGNITO_CLIENT_ID` in their Lambda environment, so `Settings._local_auth_is_local_only` fails closed with `ValidationError` on every invocation (and in `prod`, `Settings._cognito_is_fully_configured` line 292 also rejects the default `http://localhost:3000` `cors_origins`).
  - *(Correction to earlier draft: the validation failure in `get_settings()` is caused by `Settings._local_auth_is_local_only` / `_cognito_is_fully_configured`, not `RATE_LIMIT_TABLE_NAME`, because `rate_limit_table_name` is checked in `container._build_rate_limit_store()` rather than a `Settings` validator.)*

#### Confirmed Defect 2 — `IngestionStack` Omits `LLM_PROVIDER="bedrock"` and Bedrock Embedding IAM Permissions on `ProcessingFunction`

- **Files & Lines:**
  - `infrastructure/stacks/ingestion_stack.py` (lines 105–116):
    ```python
        processing_env = {
            "RATE_LIMIT_TABLE_NAME": admission_table_name,
            "ENVIRONMENT": environment_name,
            "AWS_AURORA_CLUSTER_ARN": aurora_cluster_arn,
            "AWS_AURORA_SECRET_ARN": aurora_secret_arn,
            "DB_NAME": "cloudguard",
            "DOCUMENT_STORE": "s3",
            "AWS_DOCUMENTS_BUCKET": self.documents_bucket.bucket_name,
            "EVENT_PUBLISHER": "eventbridge",
            "AWS_EVENT_BUS_NAME": self.event_bus.event_bus_name,
            "VECTOR_STORE": "s3_vectors" if s3_vector_index_arn else "pgvector",
        }
    ```
  - `backend/app/deployed_document_worker.py` (lines 40–42 and 116):
    ```python
    _settings = get_worker_settings()
    configure_logging(_settings)
    _container = build_worker_container(_settings)
    ...
            service = DocumentProcessingService(
                ...
                embedding_provider=_container.embeddings,
                ...
            )
    ```
  - `backend/app/core/config.py` (lines 330–337):
    ```python
    class WorkerSettings(BaseSettings):
        ...
        llm_provider: Literal["mock", "recorded", "bedrock"] = "mock"
    ```
  - `backend/app/core/container.py` (lines 102–118):
    ```python
    def _build_embeddings(
        settings: Settings | WorkerSettings | AgentWorkerSettings,
    ) -> EmbeddingProvider:
        match settings.llm_provider:
            case "mock":
                return MockEmbeddingProvider(dimensions=settings.bedrock.embedding_dimensions)
            ...
            case "bedrock":
                from app.adapters.bedrock.embedding import BedrockEmbeddingProvider
    ```
- **Verified Runtime Consequence:**
  - Because `IngestionStack` does not set `"LLM_PROVIDER": "bedrock"` (or `"BEDROCK_EMBEDDING_MODEL": embedding_model_id`) on `self.processing_function` and does not grant `bedrock:InvokeModel` on `amazon.titan-embed-text-v2:0` (unlike `AgentWorkflowStack.research_function` at `agent_workflow_stack.py` lines 70, 85, and 239–245), `cloudguard-document-processor-<env>` defaults `WorkerSettings.llm_provider` to `"mock"`.
  - Furthermore, unlike `Settings` (line 261) and `AgentWorkerSettings` (line 372), `WorkerSettings` has no `_no_mock_llm_in_prod` validator. As a result, deployed document ingestion in `dev` or `prod` would silently write deterministic hash embeddings (`MockEmbeddingProvider`) into Aurora `pgvector` / S3 Vectors while `ResearchFunction` queries with `BedrockEmbeddingProvider` (`amazon.titan-embed-text-v2:0`).

#### Confirmed Defect 3 — `Environment` Enum in `backend/app/core/config.py` Rejects `"staging"`, While `deploy.yml` and `infrastructure/app.py` Support `staging`

- **Files & Lines:**
  - `backend/app/core/config.py` (lines 25–28):
    ```python
    class Environment(StrEnum):
        LOCAL = "local"
        DEV = "dev"
        PROD = "prod"
    ```
  - `.github/workflows/deploy.yml` (lines 31–34 and 95):
    ```yaml
            options:
              - dev
              - staging
              - prod
    ```
  - `infrastructure/app.py` (line 105):
    ```python
    is_production_like = environment_name in ("prod", "staging") or environment_name != "dev"
    ```
- **Verified Runtime Consequence:**
  - Synthesizing and deploying CDK stacks for `environment="staging"` sets `ENVIRONMENT="staging"` on all 7 Lambda workers, which causes `Settings`, `WorkerSettings`, and `AgentWorkerSettings` to fail Pydantic enum validation at cold start (`Input should be 'local', 'dev' or 'prod'`).

#### Confirmed Defect 4 — Upstream Amazon Linux 2023 Base Image Packages (`glibc*`, `rpm*`) in `backend/Dockerfile` Fail the Blocking CI Trivy Scan

- **Files & Lines:**
  - `backend/Dockerfile` (lines 7–16):
    ```dockerfile
    FROM public.ecr.aws/lambda/python:3.12

    COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
    COPY app ${LAMBDA_TASK_ROOT}/app

    RUN pip install --no-cache-dir ${LAMBDA_TASK_ROOT}

    CMD ["app.lambda_handler.handler"]
    ```
  - `.github/workflows/ci.yml` (lines 224–234): builds `cloudguard-backend:ci` and runs Trivy with `exit-code: '1'`, `vuln-type: 'os,library'`, `severity: 'CRITICAL,HIGH'`, `trivyignores: '.trivyignore.yaml'`.
- **Verified Consequence:**
  - As documented in Section 2.7.B, `public.ecr.aws/lambda/python:3.12@sha256:7236d211...` (`amazon 2023.12.20261026`) contains `glibc*` `2.34-231.amzn2023.0.5` (fixed in `.0.6`, `CVE-2026-86805`) and `rpm*` `4.16.1.3-29.amzn2023.0.8` (fixed in `.0.9`, `CVE-2026-103242`, `CVE-2026-84837`, `CVE-2026-95519`), producing `10` `HIGH` findings and `exit code 1` until `dnf upgrade` is added to `backend/Dockerfile` or AWS updates the base image tag.

---

### 3.2 What the Current CDK Application Provisions vs. Intentionally Deferred / External Infrastructure

`infrastructure/app.py` synthesizes **6 CloudFormation stacks** (`7` out-of-VPC Lambda worker functions total):

| CDK Stack | Provisioned Resources (Verified in `infrastructure/stacks/*.py`) | External Prerequisites / Deferred Scope |
| :--- | :--- | :--- |
| **`CloudGuardIdentity-<env>`** (`identity_stack.py`) | - Cognito User Pool (`cloudguard-<env>`, self-sign-up disabled, `custom:organization_id`, `custom:department`, `FeaturePlan.PLUS`, `AUDIT_ONLY` threat protection, optional TOTP MFA)<br>- Role groups: `analyst`, `manager`, `admin`<br>- SPA client (`cloudguard-web-<env>`, Authorization Code + PKCE, no client secret, 1-hour access/ID tokens)<br>- Cognito Hosted UI domain (`cloudguard-<env>-<account_suffix>.auth.<region>.amazoncognito.com`) | - `_callback_urls` (lines 170–179) returns `["http://localhost:3000/login/"]` for `dev` and `[f"https://{environment_name}.cloudguard.example/login/"]` for non-`dev`; must be updated/parameterized once a real frontend origin exists.<br>- Initial tenant organizations and Cognito user accounts/group memberships are operator-provisioned. |
| **`CloudGuardRateLimits-<env>`** (`rate_limit_stack.py`) | - DynamoDB table `cloudguard-rate-limits-<env>` (`PAY_PER_REQUEST`, PK `pk`, TTL `ttl`, PITR enabled, AWS-managed encryption)<br>- Helper `grant_admission(grantee)` and `grant_worker_admission(grantee, table_arn=...)` | - Worker admission is granted to `ProcessingFunction`, `ResearchFunction`, `RiskFunction`, and `ReviewerFunction`.<br>- `rate_limits.grant_admission(api_role)` (noted in `infrastructure/app.py` lines 51–52) awaits a deployed API compute construct. |
| **`CloudGuardIngestion-<env>`** (`ingestion_stack.py`) | - S3 bucket `cloudguard-documents-<env>-<account>` (`S3_MANAGED` encryption, versioned, SSL-only, block public access, EventBridge notifications enabled)<br>- SQS DLQ `cloudguard-ingestion-dlq-<env>` (14-day retention)<br>- Custom EventBridge bus `cloudguard-events`<br>- Out-of-VPC Docker Lambda `cloudguard-document-processor-<env>` (`app.deployed_document_worker.handler`)<br>- Step Functions Standard state machine `cloudguard-ingestion-<env>` (`NormalizeInput` → `ProcessDocument` → catch to `SendFailureToDLQ`) triggered by default-bus S3 `Object Created` rule `cloudguard-ingestion-trigger-<env>` | - Requires external Aurora Serverless v2 PostgreSQL 16 cluster ARN (`aurora_cluster_arn`) and Secrets Manager ARN (`aurora_secret_arn`).<br>- Requires Confirmed Defect #2 fix (`LLM_PROVIDER="bedrock"` + `bedrock:InvokeModel` on `amazon.titan-embed-text-v2:0`). |
| **`CloudGuardAgentWorkflow-<env>`** (`agent_workflow_stack.py`) | - 6 out-of-VPC Docker Lambda functions:<br>  1. `cloudguard-agent-research-<env>` (`app.deployed_research_agent_worker.handler`)<br>  2. `cloudguard-agent-risk-<env>` (`app.deployed_risk_agent_worker.handler`)<br>  3. `cloudguard-agent-reviewer-<env>` (`app.deployed_reviewer_agent_worker.handler`)<br>  4. `cloudguard-agent-approval-<env>` (`app.deployed_approval_creation_worker.handler`, `WAIT_FOR_TASK_TOKEN`)<br>  5. `cloudguard-approval-callback-recovery-<env>` (`app.deployed_approval_callback_recovery_worker.handler`, 1-minute EventBridge schedule)<br>  6. `cloudguard-approved-action-execution-<env>` (`app.deployed_approved_action_execution_worker.handler`)<br>- Step Functions Standard state machine `cloudguard-agent-workflow-<env>` (`ResearchAgent` → `RiskAgent` → `ReviewerAgent` → `WaitForManagerApproval` → `ExecuteApprovedAction`) | - Requires external `aurora_cluster_arn` and `aurora_secret_arn`.<br>- Requires `bedrock_guardrail_id` and `bedrock_guardrail_version` in `prod` (enforced by `AgentWorkerSettings._production_guardrail_is_required` and `deploy.yml` lines 185–190).<br>- Requires Confirmed Defect #1 fix (`get_agent_worker_settings()` in recovery and execution workers). |
| **`CloudGuardObservability-<env>`** (`observability_stack.py`) | - CloudWatch Dashboard `CloudGuard-Operations-<env>`<br>- 7 CloudWatch Alarms (`cloudguard-ingestion-dlq-alarm-<env>`, `cloudguard-ingestion-failed-<env>`, `cloudguard-processor-lambda-errors-<env>`, `cloudguard-agent-workflow-failed-<env>`, `cloudguard-agent-workflow-throttled-<env>`, `cloudguard-rate-limit-table-errors-<env>`, `cloudguard-admission-denials-spike-<env>`) | - Alarms do not have SNS notification actions attached yet. |
| **`CloudGuardOIDC-<env>`** (`oidc_stack.py`) | - GitHub OIDC Provider (`https://token.actions.githubusercontent.com`)<br>- IAM Role `cloudguard-github-deploy-<env>` scoped to `repo:aaradhyapartap/cloudguard-ai:environment:<env>`, permitted to assume CDK bootstrap roles (`cdk-hnb659fds-*-role-<account>-<region>`) and inspect CloudGuard resources | - Requires one-time operator `cdk bootstrap` (qualifier `hnb659fds`) and initial `CloudGuardOIDC-<env>` deployment before GitHub Actions can assume `AWS_DEPLOY_ROLE_ARN`. |

---

### 3.3 Deferred Interactive HTTP API & Frontend Hosting Boundary (Architectural Alignment with `AGENTS.md` & `ADR-0008`)

Per `AGENTS.md` (lines 33–37) and `ADR-0008` (`docs/adr/0008-lambda-outside-vpc-via-aurora-data-api.md`):
- **Deployed Lambda functions must remain outside the database VPC.**
- **Deployed Lambda database access must use the Aurora Serverless v2 RDS Data API.**
- **Do not deploy the `asyncpg`-backed `tenant_session()` inside an out-of-VPC Lambda.**
- **Never provision a NAT Gateway for this architecture.**

#### Current State of the Interactive HTTP API (`backend/app/api/v1/`)
1. **Packaging & Entrypoint Exist:** `backend/app/lambda_handler.py` defines `handler = Mangum(create_app(), lifespan="off")`, and `backend/Dockerfile` sets `CMD ["app.lambda_handler.handler"]`.
2. **No CDK API Compute or Frontend Hosting Stack Exists Yet:** As noted in `infrastructure/app.py` lines 51–52 (`# This repository does not yet define an API Lambda construct`) and `docs/development/phase12-handoff.md` (§3.3), the current 6 CDK stacks do not provision an API Lambda, API Gateway HTTP API, or S3/CloudFront frontend hosting for `frontend/out/`.
3. **4 Persistence Ports + Document/Principal HTTP Dependencies Are Currently Local/`SQLAlchemy`-Only (`asyncpg` TCP):**
   - `backend/app/api/dependencies.py` (`get_principal` → `provision_principal_in_tenant` via `tenant_session`)
   - `backend/app/repositories/document_repository.py` (`DocumentRepository` via `AsyncSession`)
   - `backend/app/adapters/local/compliance_repository.py` (`SQLAlchemyComplianceRepository` implementing `ComplianceRepository`)
   - `backend/app/adapters/local/approval_repository.py` (`SQLAlchemyApprovalRepository` implementing `ApprovalRepository` for interactive HTTP list/get/decide)
   - `backend/app/adapters/local/evaluation_repository.py` (`SQLAlchemyEvaluationRepository` implementing `EvaluationRepository`)
   - `backend/app/adapters/local/approved_action_executor.py` (`SQLAlchemyApprovedActionExecutor` implementing `ApprovedActionExecutor`)
4. **Architecture-Consistent Path to a Hosted Interactive API:**
   - Because `AGENTS.md` strictly forbids deploying `asyncpg`-backed `tenant_session()` in an out-of-VPC Lambda and forbids provisioning a NAT Gateway, hosting the interactive FastAPI HTTP layer on AWS Lambda requires implementing Aurora RDS Data API adapters (`AuroraDataAPIClient`, matching the existing 5 adapters in `backend/app/adapters/aws/`) for principal provisioning, `DocumentRepository`, `ComplianceRepository`, `ApprovalRepository`, and `EvaluationRepository`, then adding an out-of-VPC API Lambda + API Gateway HTTP API + CloudFront/S3 static frontend stack that calls `rate_limits.grant_admission(api_lambda)` and injects `RATE_LIMIT_TABLE_NAME`, `IDENTITY_PROVIDER="cognito"`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, and `COGNITO_HOSTED_UI_DOMAIN`.

---

## Part 4: Smallest Correction Inventory for Confirmed Defects

Without expanding scope to new infrastructure stacks, the **smallest correction inventory** to resolve all 4 confirmed defects in the existing codebase is limited to **5 files**:

| # | File | Minimal Change | Defect Resolved |
| :--- | :--- | :--- | :--- |
| **1** | `backend/app/deployed_approval_callback_recovery_worker.py` | Replace `from app.core.config import get_settings` with `from app.core.config import get_agent_worker_settings` and call `get_agent_worker_settings()` in `_build_repository()` and `_build_callback()`. | Fixes `Settings._local_auth_is_local_only` startup `ValidationError` in `cloudguard-approval-callback-recovery-<env>`. |
| **2** | `backend/app/deployed_approved_action_execution_worker.py` | Replace `from app.core.config import get_settings` with `from app.core.config import get_agent_worker_settings` and call `get_agent_worker_settings()` in `_build_repository()` and `_handle()`. | Fixes `Settings._local_auth_is_local_only` startup `ValidationError` in `cloudguard-approved-action-execution-<env>`. |
| **3** | `infrastructure/stacks/ingestion_stack.py` (and `infrastructure/app.py` if passing `embedding_model_id`) | Add `"LLM_PROVIDER": "bedrock"` and `"BEDROCK_EMBEDDING_MODEL": embedding_model_id` to `processing_env`, and grant `bedrock:InvokeModel` on `arn:aws:bedrock:<region>::foundation-model/<embedding_model_id>` to `self.processing_function`. (Optionally add `_no_mock_llm_in_prod` validator to `WorkerSettings` in `backend/app/core/config.py`.) | Prevents `cloudguard-document-processor-<env>` from silently using `MockEmbeddingProvider` in deployed environments. |
| **4** | `backend/app/core/config.py` | Add `STAGING = "staging"` to `class Environment(StrEnum)` (and include `Environment.STAGING` alongside `Environment.PROD` in production-like validators where appropriate). | Aligns backend `Environment` enum with `.github/workflows/deploy.yml` and `infrastructure/app.py` (`dev`, `staging`, `prod`). |
| **5** | `backend/Dockerfile` | Add `RUN dnf upgrade --releasever=latest -y glibc glibc-common glibc-langpack-en glibc-minimal-langpack rpm rpm-libs && dnf clean all` before `pip install`. | Patches the `10` `HIGH` Amazon Linux 2023 OS package CVEs (`CVE-2026-86805`, `CVE-2026-103242`, `CVE-2026-84837`, `CVE-2026-95519`) so the blocking Trivy container scan passes (`exit code 0`) without adding scanner exceptions. |

---

## Part 5: Separation of Tasks & Operator Decisions Required Before Cloud Deployment

### 5.1 Separation of Responsibilities
1. **Code Implementation Tasks:**
   - Apply the 5-file correction inventory in Part 4 (and update corresponding unit/CDK tests in `backend/tests/unit/` and `infrastructure/tests/`).
   - If deploying the interactive FastAPI HTTP API and Next.js SPA (beyond the 6 control-plane/worker stacks), implement RDS Data API adapters for the HTTP persistence ports and add the out-of-VPC API Lambda + API Gateway + S3/CloudFront stack and parameterized Cognito `_callback_urls`.
2. **Operator Configuration Tasks (AWS & GitHub):**
   - Bootstrap AWS account (`cdk bootstrap` with qualifier `hnb659fds`) and deploy `CloudGuardOIDC-<env>` once.
   - Provision Aurora Serverless v2 PostgreSQL 16 (`enable_http_endpoint=True` for RDS Data API, `pgvector` extension), create the `cloudguard_app` (`NOBYPASSRLS`) role and Secrets Manager secret, and run `alembic upgrade head` (`0001`–`0013`) using the schema-owner role.
   - Enable Amazon Bedrock model access (`anthropic.claude-haiku-4-5-20251001-v1:0`, `amazon.nova-pro-v1:0`, `amazon.titan-embed-text-v2:0`) and create a numbered Bedrock Guardrail version for `prod`.
   - Configure GitHub Environment variables (`AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `AWS_AURORA_CLUSTER_ARN`, `AWS_AURORA_SECRET_ARN`, and for `prod`: `BEDROCK_GUARDRAIL_ID`, `BEDROCK_GUARDRAIL_VERSION`).
3. **Live Verification Tasks (Post-Deploy):**
   - Execute `.github/workflows/deploy.yml` (`action=diff`, then `action=deploy`).
   - Verify S3 upload → EventBridge → `cloudguard-ingestion-<env>` Step Functions → `cloudguard-document-processor-<env>` Data API chunking & Titan v2 embedding persistence.
   - Verify `cloudguard-agent-workflow-<env>` Step Functions (`Research` → `Risk` → `Reviewer` → `WaitForManagerApproval` → callback recovery / Manager decision → `ExecuteApprovedAction`) and confirm CloudWatch alarms remain `OK`.

### 5.2 Decisions Requiring User Input
1. **AWS Account ID & Target Region:** Which AWS account and region (default `us-east-1`) should be targeted, and are `claude-haiku-4-5`, `nova-pro`, and `titan-embed-text-v2` enabled there?
2. **Deployment Scope (6 Existing Worker/Control-Plane Stacks vs. Full Hosted UI + HTTP API):** Should the initial AWS deployment cover the 6 implemented CDK stacks (`Identity`, `RateLimits`, `Ingestion`, `AgentWorkflow`, `Observability`, `OIDC`), or should the out-of-VPC Data API HTTP repositories and API Gateway / CloudFront hosting constructs be implemented first?
3. **Vector Backend (`pgvector` vs. `s3_vectors`):** Should the target environment use Aurora `pgvector` (default when `s3_vector_index_arn` is omitted) or an external Amazon S3 Vectors index (`s3_vector_index_arn`)?
4. **Aurora Serverless v2 ACU & Lifecycle Budget:** What min/max ACU bounds (`0.5` min ACU with auto-pause vs. continuous availability) and teardown schedule should govern the Aurora Serverless v2 cluster?

---

## Part 6: Implemented Deployment-Readiness Corrections & Post-Fix Validation (`fix/deployment-runtime-readiness`)

On local branch `fix/deployment-runtime-readiness` (branched from `main` at `10d0e5d331439bc417a188121cd17213b2f05010`), all **4 confirmed source-level deployment-readiness defects** identified in Part 3.1 were resolved and validated across **independent backend-only and infrastructure-only environments** without modifying deployed infrastructure or touching the live demo database.

### 6.1 Summary of Implemented Fixes

1. **Slice 1 — Scoped Non-Model Approval Worker Configuration (`ApprovalWorkerSettings`):**
   - Added `ApprovalWorkerSettings` and `@lru_cache def get_approval_worker_settings() -> ApprovalWorkerSettings` in `backend/app/core/config.py` (and updated `configure_logging` typing in `backend/app/core/logging.py`).
   - Updated `backend/app/deployed_approval_creation_worker.py`, `backend/app/deployed_approval_callback_recovery_worker.py`, and `backend/app/deployed_approved_action_execution_worker.py` to use `get_approval_worker_settings()` instead of API-wide `get_settings()` or model-requiring `get_agent_worker_settings()`.
   - Non-model approval workers (`creation`, `callback_recovery`, `approved_action_execution`) no longer require Cognito, CORS, or Bedrock model/Guardrail settings to start in `dev`, `staging`, or `prod`, while preserving `ENABLE_AGENTIC_WORKFLOWS` feature gates, Aurora RDS Data API access (`AWS_AURORA_CLUSTER_ARN`, `AWS_AURORA_SECRET_ARN`, `DB_NAME`), approval checks, idempotency, callback readiness safety, and `@sanitize_worker_failures`.

2. **Slice 2 — Deployed Ingestion Bedrock Embedding Configuration & Guardrails Against Unintended Mock Embeddings:**
   - Updated `infrastructure/stacks/ingestion_stack.py` and `infrastructure/app.py` to pass `embedding_model_id` (default `"amazon.titan-embed-text-v2:0"`) consistently to both `IngestionStack` and `AgentWorkflowStack`.
   - Configured `IngestionStack.processing_function` environment with `"LLM_PROVIDER": "bedrock"` and `"BEDROCK_EMBEDDING_MODEL": embedding_model_id`.
   - Granted model-scoped `bedrock:InvokeModel` permission on `arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/${embedding_model_id}` to `IngestionStack.processing_function`.
   - Added validators `_no_test_embeddings_in_prod_or_staging` and `_deployed_s3_ingestion_requires_bedrock_embeddings` on `WorkerSettings` in `backend/app/core/config.py` so deployed S3 ingestion (`document_store == "s3"` outside `local`) and `staging`/`prod` document workers fail fast if `llm_provider != "bedrock"`.
   - Preserved `1024` embedding dimensions, default model IDs (`amazon.titan-embed-text-v2:0`), and existing stored vectors.
   - **Reindex Advisory:** Any environment where documents were previously indexed using `MockEmbeddingProvider` (`llm_provider="mock"`) would contain deterministic hash vectors incompatible with `amazon.titan-embed-text-v2:0` query vectors and would require a separately authorized reindex before semantic retrieval. No reindex was performed in this task.

3. **Slice 3 — End-to-End `staging` Environment Compatibility:**
   - Added `STAGING = "staging"` to `Environment(StrEnum)` in `backend/app/core/config.py`.
   - Enforced production-equivalent security protections for `Environment.STAGING` across `Settings`, `WorkerSettings`, and `AgentWorkerSettings`:
     - `identity_provider="local"` remains strictly restricted to `Environment.LOCAL` (refused in `dev`, `staging`, and `prod`).
     - `llm_provider` must be `"bedrock"` in both `staging` and `prod` across `Settings`, `WorkerSettings`, and `AgentWorkerSettings` (`mock` and `recorded` providers are rejected).
     - `cors_origins` in `Settings` must use `https://` origins in both `staging` and `prod`.
     - `docs_enabled` defaults to `False` in both `staging` and `prod`.
     - Bedrock Guardrail ID/version remain strictly required in `prod` and validated as a complete pair whenever supplied in `dev` or `staging` (matching `AgentWorkflowStack` and `.github/workflows/deploy.yml`).

4. **Slice 4 — Container Vulnerability Remediation (`backend/Dockerfile`):**
   - Verified that `public.ecr.aws/lambda/python:3.12` on ECR Public resolves to `sha256:7236d211c350c63b264efb59f4a257a34acc4a28454f556488981918bb96b394` (`Amazon Linux 2023.12.20261026`), which locks `$releasever` to `2023.12.20261026` where `glibc` (`2.34-231.amzn2023.0.5`) and `rpm` (`4.16.1.3-29.amzn2023.0.8`) are unpatched.
   - Pinned `backend/Dockerfile` to `public.ecr.aws/lambda/python:3.12@sha256:7236d211c350c63b264efb59f4a257a34acc4a28454f556488981918bb96b394` and added a minimal OS package upgrade step (`dnf --releasever=latest upgrade -y glibc glibc-common glibc-langpack-en glibc-minimal-langpack rpm rpm-libs && dnf clean all && rm -rf /var/cache/dnf`), upgrading only the 6 affected AL2023 packages to `glibc-2.34-231.amzn2023.0.6` and `rpm-4.16.1.3-29.amzn2023.0.9`.
   - Preserved `.trivyignore.yaml` without adding any new ignore rules.

5. **Test-Environment Independence (`backend/` vs. `infrastructure/`):**
   - Removed temporary `aws_cdk` / `stacks` imports from `backend/tests/unit/test_config.py`; backend tests now validate Pydantic settings (`WorkerSettings`, `AgentWorkerSettings`, `ApprovalWorkerSettings`) and `BedrockEmbeddingProvider` container wiring for `dev`, `staging`, and `prod` using only backend dependencies.
   - Added `test_synthesized_worker_environment_contracts_across_dev_staging_and_prod` in `infrastructure/tests/test_agent_workflow_stack.py` to keep CloudFormation/CDK synthesis assertions inside the infrastructure test suite.

---

### 6.2 Post-Fix Validation Matrix (Separate Environments, Exact Commands, Exit Codes, and Totals)

| Check | Exact Command | Isolated / Runtime Context | Exit Code | Result Summary |
| :--- | :--- | :--- | :--- | :--- |
| **Backend Venv Dependency Isolation** | `pip install -e "./backend[dev]" psycopg2-binary` + `python -c "import aws_cdk"` | Fresh temporary venv (`cg-venv-backend`, local Python 3.14.3; Backend CI dependency install only — CI uses Python 3.12) | `0` (`ModuleNotFoundError`) | `VERIFIED_NO_AWS_CDK_IN_BACKEND_VENV` (`aws_cdk` not installed) |
| **Backend Ruff** | `python -m ruff check .` (in `backend/`) | Fresh backend-only venv & host `ruff 0.16.4` | `0` | `All checks passed!` |
| **Backend mypy (CI target)** | `python -m mypy app tests/security` (in `backend/`) | Fresh backend-only venv (exact Backend CI scope) | `0` | `Success: no issues found in 166 source files` |
| **Backend + Scripts mypy** | `python -m mypy app tests/security ../scripts/benchmark_vector_stores.py ../scripts/verify_deployment_ci.py` | Pre-test-split runtime check (`mypy 2.4.1`; app/scripts unchanged) | `0` | `Success: no issues found in 168 source files` |
| **Affected Backend Unit Tests** | `python -m pytest tests/unit/test_config.py tests/unit/test_deployed_document_worker.py tests/unit/test_deployed_approval_creation_worker.py tests/unit/test_deployed_approval_callback_recovery_worker.py tests/unit/test_deployed_approved_action_execution_worker.py tests/unit/test_s3_vector_store.py -ra` | Fresh backend-only venv (`cg-venv-backend`, no `aws_cdk`) | `0` | **`90 passed, 0 failed, 0 skipped`** in `2.86s` |
| **Backend Security Regressions (Isolated DB)** | `python -m pytest tests/security -ra` (`127.0.0.1:55432`, `DB_USER=cloudguard_app`, `RUN_DB_TESTS=1`, `REQUIRE_SECURITY_DB_TESTS=1`) | Fresh backend-only venv + ephemeral `pgvector/pg16` container | `0` | **`109 passed, 0 failed, 0 skipped`** in `3.03s` |
| **Full Backend Suite (Isolated DB)** | `python -m pytest -ra` (`127.0.0.1:55432`, `DB_USER=cloudguard_app`, `RUN_DB_TESTS=1`, `REQUIRE_SECURITY_DB_TESTS=1`) | Fresh backend-only venv + ephemeral `pgvector/pg16` (migrated `0001`–`0013` & seeded) | `0` | **`1,412 passed, 0 failed, 0 skipped`** in `50.67s` (temporary DB removed) |
| **Infrastructure mypy** | `python -m mypy stacks tests` (in `infrastructure/`) | Separate infrastructure venv (`cg-venv-infra`: `pip install -r infrastructure/requirements.txt pytest mypy`) | `0` | `Success: no issues found in 18 source files` |
| **Affected Infrastructure CDK Tests** | `python -m pytest tests/test_ingestion_stack.py tests/test_agent_workflow_stack.py -ra` | Separate infrastructure venv (`pytest 9.2.0`) | `0` | **`38 passed, 0 failed, 0 skipped`** in `175.92s` |
| **Full Infrastructure Pytest Suite** | `python -m pytest -ra` (in `infrastructure/`) | Separate infrastructure venv (`pytest 9.2.0`) | `0` | **`66 passed, 0 failed, 0 skipped`** in `168.88s` (`+3` total vs. `63` baseline: `+2` ingestion, `+1` workflow contracts) |
| **Offline CDK Synthesis** | `CDK_OFFLINE_SYNTH=1 python app.py` (in `infrastructure/`) | Host environment (verified after all `app.py` and `stacks/` edits) | `0` | Synthesized all 6 stacks cleanly |
| **Docker Image Build (`--pull`)** | `docker build --pull -t cloudguard-backend:ci backend` | Unchanged runtime/Dockerfile (`sha256:7236d211...` AL2023 base) | `0` | Built image `sha256:6f9fa2f918fc3d507c38322a061364cd964aea0c814f5dae9cb32a98ae79904a` (`glibc-2.34-231.amzn2023.0.6`, `rpm-4.16.1.3-29.amzn2023.0.9`) |
| **Pinned Trivy Container Scan** | `docker run --rm -v //var/run/docker.sock:/var/run/docker.sock -v "${PWD}/.trivyignore.yaml:/.trivyignore.yaml:ro" aquasec/trivy:0.69.3 image --scanners vuln --ignorefile /.trivyignore.yaml --severity CRITICAL,HIGH --exit-code 1 cloudguard-backend:ci` | `aquasec/trivy:0.69.3` (remediated image unchanged since scan) | `0` | **`0` unignored `HIGH`/`CRITICAL` vulnerabilities (`0` OS, `0` Python, `0` Go)** |
| **Container Worker Import & Startup Smoke Checks** | `docker run --rm --entrypoint python ... cloudguard-backend:ci -c "import app.deployed_document_worker ..."` | `prod` & `staging` envs without Cognito/CORS/Guardrail or live AWS | `0` | `CONTAINER_PROD_WORKER_SMOKE_OK` and `CONTAINER_STAGING_ALL_WORKERS_SMOKE_OK` |

---

### 6.3 Changed-File Inventory (`fix/deployment-runtime-readiness`)

| Path | Category | Summary of Changes |
| :--- | :--- | :--- |
| `backend/app/core/config.py` | Runtime Configuration | Added `Environment.STAGING`; enforced `staging` protections on `Settings`, `WorkerSettings`, and `AgentWorkerSettings`; enforced `LLM_PROVIDER='bedrock'` for deployed S3 ingestion in `WorkerSettings`; added `ApprovalWorkerSettings` and `get_approval_worker_settings()`. |
| `backend/app/core/logging.py` | Logging | Added `ApprovalWorkerSettings` to `configure_logging` settings union type. |
| `backend/app/deployed_approval_creation_worker.py` | Deployed Worker | Switched from `get_agent_worker_settings()` to `get_approval_worker_settings()`. |
| `backend/app/deployed_approval_callback_recovery_worker.py` | Deployed Worker | Replaced API-wide `get_settings()` with `get_approval_worker_settings()` and configured structured worker logging in `handler`. |
| `backend/app/deployed_approved_action_execution_worker.py` | Deployed Worker | Replaced API-wide `get_settings()` with `get_approval_worker_settings()` and configured structured worker logging in `handler`. |
| `infrastructure/stacks/ingestion_stack.py` | CDK Infrastructure | Added `embedding_model_id` parameter (default `"amazon.titan-embed-text-v2:0"`), injected `"LLM_PROVIDER": "bedrock"` and `"BEDROCK_EMBEDDING_MODEL": embedding_model_id` into `processing_env`, and granted model-scoped `bedrock:InvokeModel` permission to `self.processing_function`. |
| `infrastructure/app.py` | CDK Application | Passed `embedding_model_id=embedding_model_id` into `IngestionStack` so ingestion and research workers share the configured embedding model. |
| `backend/Dockerfile` | Container Packaging | Pinned `public.ecr.aws/lambda/python:3.12@sha256:7236d211c350c63b264efb59f4a257a34acc4a28454f556488981918bb96b394` and upgraded `glibc*` (`2.34-231.amzn2023.0.6`) and `rpm*` (`4.16.1.3-29.amzn2023.0.9`) via `dnf --releasever=latest upgrade -y`. |
| `backend/tests/unit/test_config.py` | Unit Tests | Added `staging` configuration tests, deployed S3 mock-embedding rejection tests, `ApprovalWorkerSettings` tests, and pure backend validation of `dev`/`staging`/`prod` worker environment contracts without importing `aws_cdk` or `stacks`. |
| `backend/tests/unit/test_deployed_approval_creation_worker.py` | Unit Tests | Added `dev`/`staging`/`prod` startup test without Cognito, CORS, or Guardrail variables. |
| `backend/tests/unit/test_deployed_approval_callback_recovery_worker.py` | Unit Tests | Added `dev`/`staging`/`prod` startup test without Cognito, CORS, or Guardrail variables. |
| `backend/tests/unit/test_deployed_approved_action_execution_worker.py` | Unit Tests | Added `dev`/`staging`/`prod` startup test without Cognito, CORS, or Guardrail variables. |
| `backend/tests/unit/test_deployed_document_worker.py` | Unit Tests | Added `dev`/`staging`/`prod` test proving `build_worker_container` selects `BedrockEmbeddingProvider` with `amazon.titan-embed-text-v2:0` (`1024` dimensions). |
| `backend/tests/unit/test_s3_vector_store.py` | Unit Tests | Updated `test_worker_startup_with_synthesized_environment` `doc_env` fixture to include `"LLM_PROVIDER": "bedrock"` and `"BEDROCK_EMBEDDING_MODEL": "amazon.titan-embed-text-v2:0"` matching `IngestionStack`. |
| `infrastructure/tests/test_agent_workflow_stack.py` | CDK Template Tests | Added `test_synthesized_worker_environment_contracts_across_dev_staging_and_prod` verifying synthesized Cognito/CORS-free worker environment contracts across `dev`, `staging`, and `prod`. |
| `infrastructure/tests/test_ingestion_stack.py` | CDK Template Tests | Asserted `LLM_PROVIDER="bedrock"`, `BEDROCK_EMBEDDING_MODEL="amazon.titan-embed-text-v2:0"`, model-scoped `bedrock:InvokeModel` IAM policy, and consistency between `IngestionStack` and `AgentWorkflowStack`. |
| `docs/portfolio/FINAL_VALIDATION_AND_DEPLOYMENT_GAPS.md` | Portfolio Report | Recorded implemented fixes, separate backend/infrastructure validation outcomes, changed-file inventory, reindex advisory, and remaining deployment blockers. |

---

### 6.4 Remaining External Blockers Before Live AWS Deployment

All 6 CDK stacks and all 7 deployed Lambda workers in this repository are now source-ready and validated. Remaining items before a live cloud deployment are external prerequisites or intentionally deferred architecture:

1. **External AWS Account & Data Plane Prerequisites (Operator-Provisioned):**
   - CDK bootstrap (`cdk bootstrap` with qualifier `hnb659fds`) and initial `CloudGuardOIDC-<env>` deployment.
   - External Aurora Serverless v2 PostgreSQL 16 cluster with RDS Data API enabled (`enable_http_endpoint=True`), `pgvector` extension, Secrets Manager secret, schema migrations (`0001`–`0013`), and `cloudguard_app` (`NOBYPASSRLS`) role.
   - Amazon Bedrock model access (`anthropic.claude-haiku-4-5-20251001-v1:0`, `amazon.nova-pro-v1:0`, `amazon.titan-embed-text-v2:0`) and a numbered Bedrock Guardrail version (`BEDROCK_GUARDRAIL_ID`, `BEDROCK_GUARDRAIL_VERSION`) for `prod`.
   - GitHub Environment variables (`AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `AWS_AURORA_CLUSTER_ARN`, `AWS_AURORA_SECRET_ARN`, and `BEDROCK_GUARDRAIL_ID` / `BEDROCK_GUARDRAIL_VERSION` for `prod`).
2. **Deferred Interactive HTTP API & Frontend Hosting Architecture (Out of Scope for This Fix):**
   - As documented in Section 3.3, hosting the interactive FastAPI HTTP API and Next.js static export (`frontend/out/`) on AWS requires Aurora RDS Data API adapters for the HTTP persistence ports plus an out-of-VPC API Lambda + API Gateway + S3/CloudFront stack and parameterized Cognito `_callback_urls`.
