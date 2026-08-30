# Current Development Task

## Phase

Phase 4 - Retrieval-Augmented Generation (Completed) / Phase 5 - Deterministic Compliance & Risk Scoring (Planning)

## Branch

phase-4-rag

## Phase 4 Status

Phase 4 - Retrieval-Augmented Generation is complete, verified, and audited.

Completed capabilities include:

- **pgvector Persistence Foundation (Phase 4.1)**:
  - Alembic migration enabling `vector` extension and adding `embedding vector(1024)` column to `document_chunks`.
  - HNSW index using `vector_cosine_ops`.
  - Local `SQLAlchemyVectorStore` with pgvector cosine distance operations and RLS enforcement.
  - Deployed `AuroraDataAPIVectorStore` using parameterized `:embedding::vector` SQL.
  - Safe vector clearing (`delete_by_document` nulls embedding while preserving chunk rows).
- **Embedding Generation & Ingestion Lifecycle (Phase 4.2)**:
  - Independent `EmbeddingProvider` and `LLMProvider` abstractions.
  - Dedicated `BedrockEmbeddingProvider` for Amazon Titan Text Embeddings v2 (`amazon.titan-embed-text-v2:0`).
  - Strict ingestion lifecycle: `QUEUED -> EXTRACTING -> INDEXING -> READY`.
  - Documents transition to `READY` and publish `DocumentIndexed` only after vector persistence succeeds.
  - Concurrency-safe atomic claims (`claim_for_processing` and `claim_for_indexing`).
- **Retrieval Orchestration (Phase 4.3)**:
  - `RetrievalService` composing `EmbeddingProvider` and `VectorStore`.
  - Strict tenant scoping from `Principal.organization_id`.
  - Role-based confidentiality clearance ceiling mapping (Analyst = INTERNAL, Manager = CONFIDENTIAL, Admin = RESTRICTED).
  - API endpoint `POST /api/v1/retrieval/search` guarded by `DOCUMENT_READ`.
- **RAG Answer Generation (Phase 4.4)**:
  - `RAGService` composing `RetrievalService` and `LLMProvider`.
  - Prompt-injection resistant system prompt treating document context as untrusted reference data.
  - Deterministic source citations (`[S1]`, `[S2]`) with authoritative `RAGSource` mapping.
  - Hard upper bound enforced on reference context length with deterministic content truncation on first-chunk overflow.
  - Zero-retrieval results skip LLM generation.
  - `BedrockLLMProvider` Converse API adapter with fail-closed response validation.
  - Stable, provider-neutral error normalization (`UpstreamError`).
  - API endpoint `POST /api/v1/rag/query` guarded by `DOCUMENT_READ`.

Phase 4 final validation:

- Ruff: passed
- mypy: passed (78 source files)
- backend tests: 375 passed with RLS role
- infrastructure tests: 21 passed
- git diff --check: passed

## Current Next Task

Phase 5 Architecture & Requirements Planning — Deterministic Compliance & Risk Scoring.

### Core Architecture Invariants:

1. **Actor-Independent Deterministic Scoring**:
   - **Evidence Admission**: Tenant and clearance boundaries are strictly enforced when evidence is attached or retrieved. Admitted evidence must point to real tenant document chunks.
   - **Authoritative Scoring**: Pure mathematical calculation operating strictly on persisted `ControlAssessment` inputs and validated `EvidenceReference` IDs. The resulting score is 100% identical regardless of whether an Analyst, Manager, Admin, or background worker computes it.
   - **Evidence Visibility & Projection**: API responses project and redact evidence based on caller clearance after scoring. Any `EvidenceReference` above the caller's clearance is **completely omitted** from the visible evidence list (exposing zero document IDs, chunk IDs, filenames, snippets, confidentiality levels, or location metadata) and sets a generic `hidden_evidence_present = True` flag on the control. Presentation projection never changes score computation.

2. **Reproducibility Invariant**:
   - `same scoring_version` + `same framework/control snapshot` + `same statuses/applicability` + `same effective weights` + `same validated evidence IDs` $\implies$ **identical raw control scores, aggregate compliance score, and risk classification**, independent of caller identity. (Timestamps, snapshot IDs, and event IDs are excluded from equality).

3. **Zero-Applicable Controls & Status Semantics**:
   - `overall_score = None` (`null` in JSON) when `applicable_control_count == 0`.
   - `risk_classification = RiskClassification.NOT_SCORED` when no controls are applicable.
   - An assessment with zero applicable controls is never treated as 100% compliant or LOW risk.
   - `UNASSESSED`: Applicable control that has not yet been assessed. Scores `0.0` (conservative) and counts in the denominator.
   - `NOT_APPLICABLE`: Confirmed non-applicable control. Excluded from both numerator and denominator.
   - `DEFICIENT`: Failed control requirement. Scores `0.0` and counts in the denominator.
   - `PARTIALLY_SATISFIED`: Scores `50.0` (or `35.0` with ungrounded evidence penalty).
   - `SATISFIED`: Scores `100.0` (or `70.0` with ungrounded evidence penalty).

4. **Scoring Input Lifecycle**:
   - `Candidate`: LLM/retrieval proposes candidate statuses and quotes (non-authoritative).
   - `Accepted/Validated`: User or deterministic rule validates evidence against real tenant document chunks and admits into `control_assessments`.
   - `Computed Snapshot`: `RiskScoringEngine` computes score snapshot with `scoring_version = 'v1.0'`.

5. **RBAC & Authorization Matrix**:
   - **`Permission.COMPLIANCE_CREATE = "compliance:create"`**:
     - Assigned to: `Analyst`, `Manager`.
     - Admin does **not** receive this permission (preserves segregation of duties: Admin cannot author, compute/mutate, finalize, or override assessments).
     - Required for: `POST /api/v1/compliance/assessments`, `POST /api/v1/compliance/assessments/{id}/compute`.
   - **`Permission.RISK_READ = "risk:read"`**:
     - Assigned to: `Analyst`, `Manager`, `Admin`.
     - Required for read-only GET endpoints.
   - **`Permission.RISK_REVIEW = "risk:review"`**:
     - Assigned to: `Manager` only.
     - Required for assessment review / finalization.
   - **`Permission.RISK_MODIFY_SEVERITY = "risk:modify_severity"`**:
     - Assigned to: `Manager` only.
     - Required for score / severity overrides with mandatory justification.

### Phase 5 Architectural Slices:

1. **Phase 5.1 — Domain Models & Deterministic Scoring Engine**:
   - Define domain models: `ComplianceFramework`, `ComplianceControl`, `ControlAssessment`, `ComplianceAssessment`, `RiskFinding`, `ScoreOverride`, `EvidenceReference`, `RiskClassification.NOT_SCORED`.
   - Add `Permission.COMPLIANCE_CREATE` to authz model for Analyst and Manager.
   - Implement pure deterministic Python scoring engine (`RiskScoringEngine`) with versioned formula (`scoring_version = 'v1.0'`).
   - Unit test pure mathematical scoring, edge cases, weighting, missing evidence penalties, not-applicable exclusions, and rounding.

2. **Phase 5.2 — Persistence Schema & RLS Migrations**:
   - Add Alembic migration creating tables: `compliance_frameworks`, `compliance_controls`, `compliance_assessments`, `control_assessments`, `risk_findings`, `score_overrides`.
   - Enable PostgreSQL Row-Level Security on all tenant tables.
   - Implement repositories / persistence adapters for SQLAlchemy.

3. **Phase 5.3 — Bounded LLM Extraction & Compliance Assessment Service**:
   - `ComplianceAssessmentService` orchestrating assessment creation, automated evidence matching via `RetrievalService`, and deterministic score computation via `RiskScoringEngine`.
   - Bounded structured LLM extraction for candidate findings and summaries (LLM never assigns authoritative score).
   - Enforce tenant and confidentiality isolation at admission and visibility layers.
   - Publish domain events via `EventPublisher` (`ComplianceAssessmentCreated`, `ComplianceAssessmentComputed`, `ComplianceAssessmentOverridden`).

4. **Phase 5.4 — Compliance API & Human Override Workflow**:
   - API endpoints: `POST /api/v1/compliance/assessments`, `GET /api/v1/compliance/assessments/{id}`, `POST /api/v1/compliance/assessments/{id}/compute`, `POST /api/v1/compliance/assessments/{id}/override`.
   - Integration tests with PostgreSQL RLS role `cloudguard_app`.

## Validation

Full project validation:

powershell -ExecutionPolicy Bypass -File .\scripts\validate-all.ps1

## Commit Policy

Do not commit or push until:

- focused validation passes
- full validation passes
- git diff --check passes
- architecture review is complete
