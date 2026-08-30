# ADR-0005: Deterministic risk and compliance scoring in Python

- **Status:** accepted
- **Date:** 2026-08-30
- **Phase:** 5

## Context

Enterprise compliance evaluation requires calculating risk and control satisfaction scores across frameworks (such as SOC 2, ISO 27001, and NIST CSF).

Allowing Large Language Models to directly output numeric risk scores (e.g. asking the model "On a scale of 1 to 100, what is the risk score?") introduces significant flaws:
1. **Non-determinism**: The same document corpus can yield fluctuating scores across runs.
2. **Unexplainability**: Mathematical weights and component contributions cannot be independently verified or audited by regulators.
3. **Vulnerability to hallucination & manipulation**: Prompt injection or subtle wording variations can arbitrarily sway the score.
4. **Actor-dependent scoring hazard**: If scoring queries retrieval with the caller's clearance ceiling at runtime, an Analyst and an Admin recomputing the same assessment would obtain different scores.

The platform requires an objective, reproducible, actor-independent, and explainable compliance scoring engine.

## Options

| Option | Pros | Cons |
|---|---|---|
| LLM-Generated Holistic Score | Free-form reasoning; captures subjective nuances | Non-deterministic; non-auditable; prone to prompt drift; unversioned |
| External Rules Engine / DSL (e.g. OPA/Rego) | Declarative policy language | High operational complexity for mathematical aggregations; extra language runtime |
| Deterministic Python Scoring Engine with Versioned Formulas | 100% reproducible; unit-testable; auditable component breakdowns; versioned weights (`scoring_version`); LLM bounded to structured evidence extraction; actor-independent computation | Requires explicit formula modeling and structured schemas in domain code |

## Decision

Implement all authoritative compliance and risk scoring calculations **strictly in deterministic Python code**:

1. **Separation of Evidence Admission vs. Authoritative Scoring vs. Visibility**:
   - **Admission**: Tenant and clearance checks apply when evidence is attached or proposed. Evidence must map to genuine tenant chunks.
   - **Scoring**: Authoritative recomputation is a pure function over persisted `ControlAssessment` inputs and validated `EvidenceReference` IDs. The score is identical whether computed by an Analyst, Manager, Admin, or an automated pipeline.
   - **Visibility / Presentation**: The API presentation layer projects and filters evidence based on caller clearance after scoring. If an admitted `EvidenceReference` exceeds caller clearance, the entire reference is omitted from the visible evidence list (exposing zero document IDs, chunk IDs, filenames, snippets, confidentiality levels, or location metadata) and sets a generic `hidden_evidence_present = True` flag on the control. Presentation projection never changes score computation.
2. **Reproducibility Invariant**:
   - `same scoring_version` + `same framework/control snapshot` + `same statuses/applicability` + `same effective weights` + `same validated evidence IDs` $\implies$ **identical raw control scores, aggregate compliance score, and risk classification**, independent of caller identity. (Timestamps, snapshot IDs, and event IDs are excluded from equality).
3. **LLM Boundary**: The LLM's role is strictly bounded to proposing candidate findings, control statuses, and referencing document evidence chunks. No candidate proposal enters scoring until validated.
4. **Zero-Applicable Controls Behavior**: If an assessment has zero applicable controls, `overall_score` is `None` (`null`) and classification is `NOT_SCORED`, preventing unassessed frameworks from falsely registering as 100% compliant or LOW risk.
5. **RBAC & Segregation of Duties**:
   - `Permission.COMPLIANCE_CREATE` (`compliance:create`): Assigned to `Analyst` and `Manager` for creating assessments and computing scores. `Admin` does **not** receive this permission.
   - `Permission.RISK_READ` (`risk:read`): Read-only view for `Analyst`, `Manager`, and `Admin`.
   - `Permission.RISK_REVIEW` (`risk:review`): Assigned strictly to `Manager` for final review.
   - `Permission.RISK_MODIFY_SEVERITY` (`risk:modify_severity`): Assigned strictly to `Manager` for score overrides.
   - Human overrides record original score, override score, mandatory justification, actor ID, and timestamp in immutable `score_overrides`.

## Consequences

- **What this buys:** Complete reproducibility, regulatory auditability, mathematical explanation of every score, actor-independent results, zero restricted-metadata leakage, and immunity against LLM stochastic scoring drift.
- **What this costs:** The scoring model must be explicitly designed and parameterized in Python rather than relying on open-ended model judgements.
- **What it makes harder:** Adjusting formula weights requires versioning and deploying code updates (e.g. `'v1.1'`) rather than changing a prompt.

## Revisit when

Phase 9 evaluation harness measures whether deterministic scoring models diverge from expert human auditor ground-truth benchmarks.
