# CloudGuard AI — Reproducible Synthetic Demo Walkthrough

This guide walks through the implemented CloudGuard AI platform locally using **synthetic data only**. Every step runs offline against the local FastAPI backend (`http://localhost:8000`), Next.js console (`http://localhost:3000`), and Dockerized PostgreSQL 16 + `pgvector` database (`localhost:5432`) at **zero cloud cost**.

> **Truthfulness Note:**
> - All tenants (`Acme Manufacturing`, `Globex Industrial`), users (`*.test`), documents, compliance controls, and evaluation cases are 100% synthetic fixtures.
> - Local LLM, embedding, and evaluation runs use deterministic test/fixture providers (`MockLLMProvider`, `eval:feature-hash-1024-v1`, `eval:fixture-llm-v1`, `eval:fixture-judge-v1`), **not** live Amazon Bedrock calls.
> - No live AWS resources are required or invoked by this walkthrough.

---

## 1. Synthetic Tenants, Roles & Clearance Roster

Defined in [`backend/app/adapters/local/directory.py`](../backend/app/adapters/local/directory.py) and [`backend/app/models/principal.py`](../backend/app/models/principal.py), and seeded by [`scripts/seed_data.py`](../scripts/seed_data.py):

| Organization | Organization UUID | Email | Role | Department | Max Clearance Ceiling (`visible_confidentiality_levels`) |
|---|---|---|---|---|---|
| **Acme Manufacturing** | `11111111-1111-4111-8111-111111111111` | `analyst@acme.test` | `analyst` | Finance | `internal` (`public`, `internal`) |
| **Acme Manufacturing** | `11111111-1111-4111-8111-111111111111` | `manager@acme.test` | `manager` | Audit | `confidential` (`public`, `internal`, `confidential`) |
| **Acme Manufacturing** | `11111111-1111-4111-8111-111111111111` | `admin@acme.test` | `admin` | IT | `restricted` (`public`, `internal`, `confidential`, `restricted`) — has `DOCUMENT_READ`, **not** `AI_QUERY`, `COMPLIANCE_CREATE`, or `APPROVAL_DECIDE` |
| **Globex Industrial** | `22222222-2222-4222-8222-222222222222` | `analyst@globex.test` | `analyst` | Ops | `internal` (`public`, `internal`) — strictly isolated from Acme by PostgreSQL `FORCE ROW LEVEL SECURITY` |

---

## 2. Bootstrap Database, Provision `cloudguard_app`, Seed Data & Start Services

Run from the repository root in **Windows PowerShell**:

```powershell
# 1. Check out the implemented branch (main does not yet contain the unmerged Phase 8–12 stack)
git checkout phase-11-devops   # or phase-12-polish once published

# 2. Ensure local environment files exist
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path frontend/.env.local)) { Copy-Item frontend/.env.local.example frontend/.env.local }

# 3. Start PostgreSQL 16 + pgvector and LocalStack, with a bounded readiness check (max 30 attempts)
docker compose up -d
$Ready = $false
for ($i = 1; $i -le 30; $i++) {
    docker compose exec -T postgres pg_isready -U cloudguard -d cloudguard *> $null
    if ($LASTEXITCODE -eq 0) { $Ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $Ready) { throw "PostgreSQL did not become ready within 30 seconds." }

# 4. Provision the least-privilege runtime role (cloudguard_app, NOBYPASSRLS) BEFORE migrations
docker compose exec -T postgres psql -U cloudguard -d cloudguard -v ON_ERROR_STOP=1 -c "
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cloudguard_app') THEN
    CREATE ROLE cloudguard_app LOGIN PASSWORD 'cloudguard_app' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  ELSE
    ALTER ROLE cloudguard_app WITH LOGIN PASSWORD 'cloudguard_app' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END
`$`$;
GRANT CONNECT ON DATABASE cloudguard TO cloudguard_app;
GRANT USAGE ON SCHEMA public TO cloudguard_app;
"

# 5. Apply Alembic migrations (0001 through 0013) and seed synthetic tenants, users, and baseline evaluations
#    using SCHEMA-OWNER credentials (cloudguard)
$env:DB_USER="cloudguard"
$env:DB_PASSWORD="cloudguard"
Set-Location backend
python -m alembic upgrade head
Set-Location ..
python scripts/seed_data.py
```

In two separate terminals, start the backend API under **least-privilege runtime credentials (`cloudguard_app`)** and start the frontend SPA:

```powershell
# Terminal A — FastAPI Backend running as NOBYPASSRLS role 'cloudguard_app' (http://localhost:8000)
Set-Location backend
$env:DB_USER="cloudguard_app"
$env:DB_PASSWORD="cloudguard_app"
python -m uvicorn app.main:app --reload --port 8000

# Terminal B — Next.js Frontend (http://localhost:3000)
Set-Location frontend
npm run dev
```

---

## 3. Step-by-Step API Walkthrough (PowerShell)

Open a third PowerShell terminal to run the interactive verification steps below.

### Step 3.1 — Mint Real Signed Local JWTs (`HS256`) for All Four Synthetic Personas

In `ENVIRONMENT=local`, `/api/v1/auth/dev-login` issues real signed JWT ID tokens carrying the exact Cognito claim structure (`sub`, `cognito:groups`, `custom:organization_id`, `custom:department`, `token_use="id"`):

```powershell
$BaseUrl = "http://localhost:8000/api/v1"

function Get-DevToken([string]$Email) {
    $resp = Invoke-RestMethod -Method Post -Uri "$BaseUrl/auth/dev-login" `
        -ContentType "application/json" `
        -Body (@{ email = $Email } | ConvertTo-Json)
    return $resp.access_token
}

$AcmeAnalystToken   = Get-DevToken "analyst@acme.test"
$AcmeManagerToken   = Get-DevToken "manager@acme.test"
$AcmeAdminToken     = Get-DevToken "admin@acme.test"
$GlobexAnalystToken = Get-DevToken "analyst@globex.test"

# Verify resolved Principal and clearance levels for Acme Analyst (public, internal)
Invoke-RestMethod -Uri "$BaseUrl/me" -Headers @{ Authorization = "Bearer $AcmeAnalystToken" } | ConvertTo-Json -Depth 4
```

### Step 3.2 — Prove Server-Side RBAC & Segregation of Duties

1. **Analyst denied Admin system config (`403 Forbidden`):**
   ```powershell
   try {
       Invoke-RestMethod -Uri "$BaseUrl/system/config" -Headers @{ Authorization = "Bearer $AcmeAnalystToken" }
   } catch {
       Write-Host "Analyst on /system/config status:" $_.Exception.Response.StatusCode.value__
   }
   ```
2. **Admin allowed on `/system/config` (`200 OK`), but denied `/rag/query` (`403 Forbidden` — Admin lacks `AI_QUERY`):**
   ```powershell
   Invoke-RestMethod -Uri "$BaseUrl/system/config" -Headers @{ Authorization = "Bearer $AcmeAdminToken" } | ConvertTo-Json

   try {
       Invoke-RestMethod -Method Post -Uri "$BaseUrl/rag/query" `
           -Headers @{ Authorization = "Bearer $AcmeAdminToken" } `
           -ContentType "application/json" `
           -Body (@{ query = "What is our vendor access review policy?" } | ConvertTo-Json)
   } catch {
       Write-Host "Admin on /rag/query status:" $_.Exception.Response.StatusCode.value__
   }
   ```

### Step 3.3 — Register a Synthetic Document, Verify Missing-Object Rejection & Prove Cross-Tenant Isolation

1. **Acme Analyst registers a synthetic policy document upload (`201 Created`, initial `processing_status: "queued"`):**
   - Valid `ProcessingStatus` enum values are `queued`, `extracting`, `indexing`, `ready`, `failed`, and `quarantined`.
   - Under default `.env` (`DOCUMENT_STORE=memory`), `POST /api/v1/documents` persists the document metadata in PostgreSQL (`processing_status = "queued"`) and returns a synthetic `memory://upload/...` URI.
   ```powershell
   $DocUpload = Invoke-RestMethod -Method Post -Uri "$BaseUrl/documents" `
       -Headers @{ Authorization = "Bearer $AcmeAnalystToken" } `
       -ContentType "application/json" `
       -Body (@{
           filename              = "acme-vendor-access-policy-2026.pdf"
           content_type          = "application/pdf"
           size_bytes            = 24576
           document_type         = "policy"
           confidentiality_level = "internal"
           department            = "Finance"
           source                = "synthetic-demo"
           tags                  = @("soc2", "vendor-access")
       } | ConvertTo-Json)

   $AcmeDocId = $DocUpload.document.id
   Write-Host "Registered Acme Document ID: $AcmeDocId (status: $($DocUpload.document.processing_status))"
   ```
2. **Verify `POST /api/v1/documents/{document_id}/complete` rejects completion when no object bytes exist in `DOCUMENT_STORE=memory` (`404 Not Found`):**
   - Because `InMemoryDocumentStore` has no public HTTP `PUT` endpoint for `memory://upload/...`, `DocumentService.complete_upload` checks `head_object(key=document.storage_key)`, finds no stored object bytes, and fails closed with `404 Not Found` (`"The uploaded document object does not exist."`):
   ```powershell
   try {
       Invoke-RestMethod -Method Post -Uri "$BaseUrl/documents/$AcmeDocId/complete" `
           -Headers @{ Authorization = "Bearer $AcmeAnalystToken" }
   } catch {
       Write-Host "Complete without uploaded object status:" $_.Exception.Response.StatusCode.value__
   }
   ```
   *(To seed indexed `ready` documents and `pgvector` chunks locally without S3, run `python scripts/run_evals.py --mode local_pgvector_fixture --persist` as shown in Step 3.5 below, which populates the 7 synthetic documents and 16 `pgvector` chunks across Acme and Globex.)*
3. **Verify Cross-Tenant Isolation: Globex Analyst cannot see or fetch Acme's document (`404 Not Found` enforced by SQL predicate + PostgreSQL `FORCE RLS`):**
   ```powershell
   $GlobexList = Invoke-RestMethod -Uri "$BaseUrl/documents" `
       -Headers @{ Authorization = "Bearer $GlobexAnalystToken" }
   $GlobexIds = @($GlobexList.items | ForEach-Object { $_.id })
   Write-Host "Globex list contains Acme document ($AcmeDocId): $($GlobexIds -contains $AcmeDocId)"

   try {
       Invoke-RestMethod -Uri "$BaseUrl/documents/$AcmeDocId" `
           -Headers @{ Authorization = "Bearer $GlobexAnalystToken" }
   } catch {
       Write-Host "Globex fetch of Acme document status:" $_.Exception.Response.StatusCode.value__
   }
   ```

### Step 3.4 — Deterministic Compliance Scoring & Manager-Only Override

1. **Seed a synthetic SOC 2 compliance framework and two controls into the global catalog (idempotent, run via schema owner `cloudguard`):**
   ```powershell
   $FrameworkId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
   $Control1Id  = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"
   $Control2Id  = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"

   docker compose exec -T postgres psql -U cloudguard -d cloudguard -v ON_ERROR_STOP=1 -c "
   INSERT INTO compliance_frameworks (id, code, name, version, description)
   VALUES ('$FrameworkId', 'SOC2-DEMO', 'SOC 2 Type II (Synthetic Demo)', '2026.1', 'Synthetic demo framework')
   ON CONFLICT (code, version) DO NOTHING;

   INSERT INTO compliance_controls (id, framework_id, control_code, title, description, category, default_weight)
   VALUES
     ('$Control1Id', '$FrameworkId', 'CC6.1', 'Logical Access Revocation', 'Revoke inactive vendor accounts within 90 days.', 'Access Control', 3.0),
     ('$Control2Id', '$FrameworkId', 'CC6.2', 'Periodic Access Review', 'Conduct quarterly privileged access reviews.', 'Access Control', 2.0)
   ON CONFLICT (framework_id, control_code) DO NOTHING;
   "
   ```
2. **Acme Analyst creates an assessment (`201 Created`), updates control statuses (`200 OK`), and computes the deterministic versioned score (`v1.0`):**
   ```powershell
   $Assessment = Invoke-RestMethod -Method Post -Uri "$BaseUrl/compliance/assessments" `
       -Headers @{ Authorization = "Bearer $AcmeAnalystToken" } `
       -ContentType "application/json" `
       -Body (@{
           framework_id = $FrameworkId
           title        = "Q4 2026 Synthetic Vendor Access Audit"
       } | ConvertTo-Json)

   $AssessmentId = $Assessment.id
   $Detail = Invoke-RestMethod -Uri "$BaseUrl/compliance/assessments/$AssessmentId" `
       -Headers @{ Authorization = "Bearer $AcmeAnalystToken" }

   # Mark first control deficient and second control partially_satisfied
   $Ctrl1 = $Detail.controls[0].id
   $Ctrl2 = $Detail.controls[1].id

   Invoke-RestMethod -Method Patch -Uri "$BaseUrl/compliance/control-assessments/$Ctrl1" `
       -Headers @{ Authorization = "Bearer $AcmeAnalystToken" } `
       -ContentType "application/json" `
       -Body (@{ status = "deficient"; rationale = "Synthetic vendor account V-104 active for 173 days (>90 day policy)." } | ConvertTo-Json) | Out-Null

   Invoke-RestMethod -Method Patch -Uri "$BaseUrl/compliance/control-assessments/$Ctrl2" `
       -Headers @{ Authorization = "Bearer $AcmeAnalystToken" } `
       -ContentType "application/json" `
       -Body (@{ status = "partially_satisfied"; rationale = "Q2 review completed; Q3 sign-off pending." } | ConvertTo-Json) | Out-Null

   # Deterministically compute score in Python (records immutable snapshot #1)
   $ScoreResult = Invoke-RestMethod -Method Post -Uri "$BaseUrl/compliance/assessments/$AssessmentId/compute" `
       -Headers @{ Authorization = "Bearer $AcmeAnalystToken" }
   $ScoreResult | ConvertTo-Json -Depth 4
   ```
3. **Verify Analyst cannot override severity (`403 Forbidden`), while Manager records an immutable human score override (`201 Created`):**
   ```powershell
   try {
       Invoke-RestMethod -Method Post -Uri "$BaseUrl/compliance/assessments/$AssessmentId/override" `
           -Headers @{ Authorization = "Bearer $AcmeAnalystToken" } `
           -ContentType "application/json" `
           -Body (@{ override_overall_score = 45.0; justification = "Analyst attempt" } | ConvertTo-Json)
   } catch {
       Write-Host "Analyst override attempt status:" $_.Exception.Response.StatusCode.value__
   }

   $Override = Invoke-RestMethod -Method Post -Uri "$BaseUrl/compliance/assessments/$AssessmentId/override" `
       -Headers @{ Authorization = "Bearer $AcmeManagerToken" } `
       -ContentType "application/json" `
       -Body (@{
           override_overall_score = 35.0
           justification          = "Compensating network segmentation verified during synthetic audit review."
       } | ConvertTo-Json)
   $Override | ConvertTo-Json -Depth 4
   ```

### Step 3.5 — Inspect & Trigger Reproducible Synthetic Evaluations

1. **Query dataset metadata and trigger a reproducible fixture evaluation run via the API (`201 Created`):**
   - `EvaluationRunRead` returns boolean gate fields `security_gate_passed` and `quality_gate_advisory_passed`:
   ```powershell
   Invoke-RestMethod -Uri "$BaseUrl/evaluations/dataset" `
       -Headers @{ Authorization = "Bearer $AcmeManagerToken" } | ConvertTo-Json -Depth 4

   $EvalRun = Invoke-RestMethod -Method Post -Uri "$BaseUrl/evaluations/runs" `
       -Headers @{ Authorization = "Bearer $AcmeManagerToken" } `
       -ContentType "application/json" `
       -Body (@{ execution_mode = "deterministic_fixture" } | ConvertTo-Json)

   Write-Host "Run ID: $($EvalRun.id) | security_gate_passed: $($EvalRun.security_gate_passed) | quality_gate_advisory_passed: $($EvalRun.quality_gate_advisory_passed)"
   ```
2. **Or run the CLI evaluation harness directly against local PostgreSQL + `pgvector` (which also seeds the 7 synthetic `ready` documents and 16 `pgvector` chunks across Acme and Globex):**
   ```powershell
   $env:DB_USER="cloudguard_app"
   $env:DB_PASSWORD="cloudguard_app"
   python scripts/run_evals.py --mode local_pgvector_fixture --persist
   ```
   - **Expected Output:**
     - `Security Gate (Blocking): PASS` (`security_gate_passed = True`: `0` cross-tenant leaks, `0` unauthorized disclosures, `0` prompt-injection breaches, `16/16` adversarial cases passed)
     - `Quality Gate (Advisory): WARN` (`quality_gate_advisory_passed = False`: `precision@5: 0.7064`, `recall@10: 0.9706`, `citation_accuracy: 0.8333`, `unanswerable_abstention_rate: 0.5000`)
     - `Cost per 1,000 queries (USD): None` (unmeasured in synthetic fixture mode)

### Step 3.6 — Run the Zero-Cost Vector Store Benchmark CLI

```powershell
python scripts/benchmark_vector_stores.py --mode synthetic_fixture --num-queries 5
```

---

## 4. Browser UI Walkthrough (`http://localhost:3000`)

1. Open `http://localhost:3000/login`.
2. In `local` development mode, select any of the four synthetic personas (`analyst@acme.test`, `manager@acme.test`, `admin@acme.test`, `analyst@globex.test`) to sign in via `/api/v1/auth/dev-login`.
3. On **Dashboard (`/dashboard`)**:
   - Confirm the green **API connected** status badge, resolved `Principal` role/department/organization, and visible confidentiality levels (`public, internal` for Analyst; `public, internal, confidential` for Manager; `public, internal, confidential, restricted` for Admin).
4. On **Evaluation & Analytics (`/analytics`)**:
   - Inspect the golden dataset metadata (`golden_case_count: 50` including `8` `prompt_injection` cases, plus `adversarial_case_count: 8` in `evals/adversarial.jsonl` = `16` adversarial security checks total), blocking **Security Gate (`PASS` / `security_gate_passed: true`)**, advisory **Quality Gate (`WARN` / `quality_gate_advisory_passed: false`)**, retrieval/citation/abstention breakdowns, and provenance banner disclosing that runs executed in fixture mode (`deterministic_fixture` / `local_pgvector_fixture`) with unmeasured live inference cost (`null`).
   - Trigger a new fixture evaluation run directly from the console as `analyst@acme.test` or `manager@acme.test` (and observe that `admin@acme.test` can view team analytics runs via `ANALYTICS_READ_TEAM` but cannot trigger a new run because `Admin` does not hold `AI_QUERY`).
