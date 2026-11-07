# Model data handling policy

## Classification and authority

Document confidentiality (`public`, `internal`, `confidential`, `restricted`) and
the verified Principal's tenant and clearance remain the access-control boundary.
Sensitive-data pattern categories are separate handling annotations, not clearance
levels. Finding or redacting a pattern never grants access or downgrades a document.

| Data | Handling |
|---|---|
| Application-owned source/control identifiers | Preserve for deterministic membership/provenance checks; never treat as permission grants. |
| Stored original documents, chunks, and authorized evidence | Preserve tenant/clearance protections and original text; do not silently rewrite evidence. |
| Model-facing user requests, evidence, policy text, upstream rationales | Replace recognized sensitive spans in a copy before generation. |
| Document/query embedding inputs | Minimize before provider invocation; preserve stored original text and chunk identity. |
| Generated text/structured proposals | Reject recognized sensitive patterns before parsing, tool execution, or service return. |
| Redaction telemetry | Record policy version, surface, categories, and counts only; no matched values or raw text. |

## Deterministic policy `model-data-v1`

`app.security.sensitive_data` supplies provider-neutral detector and redaction
contracts, with a local implementation that makes no network calls. Findings carry
only validated offsets and bounded categories, not matched-value copies. Overlapping
findings merge before replacement. Invalid detector spans fail closed.

Recognized formats include email addresses, dashed US SSNs, international compact
and common US telephone formats, AKIA/ASIA access-key IDs, JWT-like strings, Bearer
credentials, labelled credential/task-token assignments, PEM private-key blocks,
URL userinfo, and common signed/capability URL query keys. Incomplete PEM private
keys are masked from the begin marker through the end of available text.

Generation uses `[REDACTED]` replacements within nonce-framed untrusted sections.
Application-selected identifier bindings are unchanged. Original evidence objects
are not mutated. All seven current generation paths use the shared input helper and
output check. Output inspection also normalizes valid JSON to catch unicode-escaped
PII and credential assignments. Rejected output produces a sanitized upstream error,
not partial model content or a silently edited structured proposal.

Ingestion detects on complete normalized text before fixed-size slicing and uses
length-preserving masking for embedding copies. Stored chunks retain original text
and gain `model_data_policy_version` and `sensitive_data_categories` annotations.
These annotations make the bounded detection decision inspectable without storing
duplicate sensitive values. Retrieval queries are independently minimized before
embedding. Existing source citations, deterministic scoring, and approval rules
remain application-controlled.

## Audit and operational limitations

Redaction events are structured operational logs, not a new durable audit ledger.
Existing PostgreSQL approval audit remains canonical and unchanged. Log retention
and access must be configured by deployment; no raw-value redaction map is retained.

Previously indexed chunks do not automatically acquire detection annotations or
minimized embeddings. Any reindex must be planned and authorized separately with
tenant-safe processing; this patch performs no database backfill. Mixed old/new
embeddings can change retrieval ranking. Local/CI and deployed adapters retain their
existing storage behavior; no schema migration or privilege change is required.

Pattern detection is not complete PII recognition, anonymization, or a guarantee
against secret leakage. Names, addresses, medical narratives, arbitrary opaque or
unlabelled task tokens/passwords, encoded/obfuscated values, fragments split across
retrieved chunks, unusual formats, and semantic inference can escape detection.
False positives are possible. Review these limits and evaluate additional detectors
before making production privacy claims. Guardrails remain defense in depth.

Authorized raw retrieval and evidence-quote responses are not generative-model
outputs and remain original evidence. Slice 3 adds metadata allowlists, generic
error-detail suppression, configured foreign-log sanitization, and worker failure
boundaries. See PROJECTIONS_AND_RUNTIME_PRIVACY.md for the review and external
runtime/IAM limits. A user with clearance may still read original evidence; this
model policy must never be mistaken for field-level access control.

## Validation

Use only synthetic sensitive values in fixtures. Tests cover supported formats,
overlapping/invalid spans, original-evidence preservation, cross-chunk ingestion,
query minimization, safe telemetry, unicode-escaped model output, and the seven
generation call sites. Full backend regression, Ruff, and mypy are required before
commit. Live AWS behavior, Python 3.12 CI, and real-world detection recall must be
verified separately; deterministic tests are not a privacy certification.
