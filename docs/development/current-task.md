# Current Development Task

## Phase

Phase 5 - Deterministic Compliance & Risk Scoring (Completed)

## Branch

phase-5-compliance-scoring

## Phase 5 Status

Phase 5 - Deterministic Compliance & Risk Scoring is complete, verified, and audited across all four sub-phases (5.1 - 5.4).

Completed capabilities include:

- **Deterministic Scoring Foundation (Phase 5.1)**:
  - Pure deterministic mathematical `RiskScoringEngine` (`scoring_version = 'v1.0'`).
  - Strict status semantics (`UNASSESSED`, `NOT_APPLICABLE`, `DEFICIENT`, `PARTIALLY_SATISFIED`, `SATISFIED`).
  - Zero-applicable controls evaluated to `overall_score = None`, `risk_classification = RiskClassification.NOT_SCORED`.
  - Missing/ungrounded evidence penalty (30% deduction).
  - Standard half-up Decimal rounding to two decimal places (`ROUND_HALF_UP`).
  - Segregation of duties RBAC: `COMPLIANCE_CREATE` for Analyst & Manager; Admin restricted to read-only `RISK_READ`.
  - Schema migration `0005_compliance_scoring.py` establishing `compliance_frameworks`, `compliance_controls`, `compliance_assessments`, `control_assessments`, `evidence_references`, and immutable `assessment_score_snapshots` with PostgreSQL RLS and trigger enforcement.

- **Persistence, Repository & Assessment Services (Phase 5.2)**:
  - `ComplianceRepository` port and `SQLAlchemyComplianceRepository` adapter.
  - Single assessment-level `FOR UPDATE` serialization discipline ensuring coherent computation snapshots and sequential revision numbering (`revision_number = MAX + 1`).
  - Immutable `AssessmentScoreSnapshot` capturing exact admitted `EvidenceReference` IDs alongside canonical scoring inputs and raw scores.
  - Strict tenant scoping and duplicate evidence prevention.

- **Bounded LLM Candidate Extraction & Projection (Phase 5.3)**:
  - `ComplianceCandidateExtractionService` providing bounded, advisory LLM candidate extraction.
  - Fail-closed structured JSON output parsing and exact quote substring verification against trusted retrieval context.
  - Deterministic input budgeting (`max_control_context_chars`, max controls, query length, retrieval context ceiling).
  - Clearance-safe projection (`ComplianceAssessmentProjection`) completely redacting evidence above caller clearance level and exposing `hidden_evidence_present`.

- **Compliance API, Review/Finalization & Immutable Human Overrides (Phase 5.4)**:
  - Migration `0006_score_overrides.py` with composite foreign keys, append-only immutability trigger (`trg_prevent_score_override_mutation`), and RLS isolation.
  - Manager-only review operations (`RISK_REVIEW` for assessment finalization to `COMPLETED`; `RISK_MODIFY_SEVERITY` for score overrides).
  - Mathematical separation of authoritative computed score vs. human reviewed override.
  - Bounded override score validation ($0.00 \dots 100.00$) with deterministic risk classification mapping via `RiskScoringEngine.classify_score()`.
  - Completed assessment immutability (rejects control mutation, evidence admission, and score recomputation).
  - FastAPI compliance router (`/api/v1/compliance`) with 12 tenant-safe, clearance-projected endpoints.
  - Full route security matrix coverage verifying role permissions across Analyst, Manager, and Admin.

Phase 5 final validation:

- Ruff: passed (0 errors)
- mypy: passed (85 source files)
- backend tests: 549 passed with tenant RLS role `cloudguard_app`
- infrastructure tests: 21 passed
- full validation script (`validate-all.ps1`): passed
- git diff --check: passed

## Validation

Full project validation:

powershell -ExecutionPolicy Bypass -File .\scripts\validate-all.ps1

## Commit Policy

Do not commit or push until:

- focused validation passes
- full validation passes
- git diff --check passes
- architecture review is complete
