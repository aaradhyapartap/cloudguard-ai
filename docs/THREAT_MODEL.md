# CloudGuard AI Threat Model

- **Version:** 1.1
- **Review date:** 2026-09-08
- **Scope baseline:** Phase 8.1 commit `9e0197c` plus Phase 8.2 model-boundary changes
- **Method:** asset and trust-boundary analysis with a qualitative likelihood/impact register

## 1. Purpose and scope

CloudGuard AI is a multi-tenant compliance intelligence platform. It ingests
organization documents, retrieves clearance-filtered evidence, uses models to
produce bounded analysis, computes authoritative risk scores in deterministic
Python, and pauses consequential action until a Manager decides.

This model covers:

- the static browser application and bearer-token handling;
- FastAPI authentication, authorization, and tenant-scoped APIs;
- PostgreSQL/pgvector persistence and Row-Level Security;
- S3 presigned upload and document processing;
- Bedrock embedding and generation adapters;
- the fixed Research -> Risk -> Reviewer workflow;
- the approval callback and approved-action execution boundary; and
- CDK resources currently present in the repository.

It does not assume the Phase 0 target diagram has been fully deployed. API Gateway,
WAF, customer-managed KMS keys, a Bedrock Guardrail resource, and a general DynamoDB
audit subsystem are target-state components, not current controls.

## 2. Security objectives

1. A caller can access only data belonging to the organization in their verified
   token and only at or below their role-derived clearance.
2. Models and retrieved content cannot gain identity, authorization, scoring,
   approval, or execution authority.
3. Consequential action exactly matches durable human authorization and cannot be
   duplicated by retry or replay.
4. Documents, prompts, PII, credentials, and task tokens do not leak across tenants,
   into unauthorized model context, or into logs and public responses.
5. Audit and deterministic score records remain attributable, immutable, and
   reconstructable.
6. Cost-bearing and resource-intensive operations remain bounded under malicious or
   accidental load.

## 3. Protected assets

| Asset | Security need | Examples |
|---|---|---|
| Tenant documents and chunks | Confidentiality, integrity, availability | Policy files, audit reports, extracted text, embeddings |
| Identity and authorization state | Integrity, authenticity | JWT claims, Cognito groups, `Principal`, permission matrix |
| Compliance and risk records | Integrity, traceability | Assessments, evidence, deterministic snapshots, overrides |
| Approval state | Integrity, non-repudiation | Proposal, Manager decision, approver, justification |
| Execution capability | Confidentiality, integrity | Step Functions task token, action projection, idempotency key |
| Audit records | Integrity, availability | Approval creation, decision, callback, execution events |
| Infrastructure credentials | Confidentiality | Workload-role credentials, Aurora secret, signing keys |
| AI inputs and outputs | Confidentiality, integrity | System prompts, retrieved context, model responses, usage |
| Platform capacity | Availability, cost control | Bedrock tokens, Lambda concurrency, database, storage |

## 4. Threat actors

| Actor | Capability and motivation |
|---|---|
| Unauthenticated internet client | Probes public routes, authentication failures, payload limits, and cost-amplification paths. |
| Authenticated tenant user | Has a valid token and attempts role escalation, excessive use, or cross-tenant/confidentiality access. |
| Malicious Manager | Holds approval authority but may attempt to execute an action outside the bounded policy. |
| Platform Admin | Has broad read/administrative access but is intentionally denied compliance judgment and approval decisions. |
| Malicious document author | Embeds prompt injection, misleading evidence, PII, secrets, or parser/resource-exhaustion payloads. |
| Compromised browser script | Can read the SPA's `sessionStorage` token and act as that user until expiry. |
| Compromised model/provider response | Returns malformed output, invented references, instructions, or sensitive content. |
| Compromised workload or AWS operator | May access environment metadata, execution history, logs, or database secrets according to IAM privileges. |
| Dependency/build attacker | Introduces vulnerable or malicious Python, npm, action, or container dependencies. |

## 5. Trust boundaries and authority

| Boundary | Untrusted side | Trusted enforcement side | Required rule |
|---|---|---|---|
| Browser -> API | Headers, body, IDs, filter values, bearer string | Identity provider, FastAPI dependencies, Pydantic models | Verify token and derive authority server-side; bound all inputs. |
| JWT -> `Principal` | Claims even when syntactically valid | Signature/issuer/audience/use checks plus claim policy | Exactly one known role and a valid provisioned organization. |
| Application -> PostgreSQL | Defective or manipulated query | Explicit tenant predicate plus forced RLS | Set transaction-local tenant context; application role cannot bypass RLS. |
| Application -> vector retrieval | Query and requested document IDs | `RetrievalService` and vector-store adapter | Organization and clearance come only from `Principal`. |
| Uploaded file -> ingestion | Bytes, filename, type declaration, embedded instructions | S3 key policy, processing service, parsers, quarantine policy | Treat all content and metadata as hostile; impose resource limits. |
| Retrieved evidence -> model | Arbitrary document text | Context builder, system policy, output validation | Evidence is data, never instruction or authority. |
| Model -> tool registry | Proposed tool name and arguments | Static allowlist, strict schema, human `Principal`, budget | Unknown or unauthorized tools fail closed. |
| Model -> compliance/risk state | Findings, scores, references | Deterministic validation and scoring services | Model output is advisory and references only admitted evidence/policy IDs. |
| Reviewer -> approval | PASS/FAIL model output | Deterministic evidence checks and trusted recommendation builder | Reviewer PASS may request approval; it cannot decide or execute. |
| Manager API -> approval row | Decision payload | Authenticated Manager, tenant session, CAS transition, DB constraints | First decision wins; identity and tenant are server-derived. |
| Approval row -> callback | Internal task token and persisted decision | Recovery worker and callback adapter | Commit decision first; never expose or log token. |
| Workflow -> executor | Execution ID and carried `Principal` | Execution repository reload and action projector | Workflow payload never carries authoritative action data. |
| Application -> logs/audit | Errors and operational metadata | Redaction/minimization policy, DB triggers | No credentials, task tokens, document bodies, prompts, or unnecessary PII. |
| Workload -> AWS | SDK calls | IAM role and resource policies | Least privilege; no model-visible credentials. |

The workflow-carried `Principal` is trusted only when the state machine can be
started by a trusted application path. The current repository does not yet include
that API/CDK entry point, so deployment IAM must restrict `states:StartExecution`.

## 6. Data flows

### 6.1 Authentication and API access

1. Cognito or the local-only signer issues an ID token.
2. FastAPI extracts the bearer token and the configured identity adapter verifies it.
3. Claims map to an immutable `Principal`; ambiguous or missing authority fails.
4. Route dependencies enforce permissions.
5. Database work occurs inside a tenant-bound transaction.

The target architecture also uses an API Gateway JWT authorizer, but it is not yet
provisioned by the current CDK source. Application verification remains mandatory
even after the gateway control is added.

### 6.2 Document ingestion and retrieval

1. An authorized Analyst or Manager registers bounded document metadata.
2. The server creates a tenant-prefixed S3 key and returns a short-lived PUT URL.
3. Upload completion triggers the ingestion workflow.
4. The worker re-resolves the tenant/document in PostgreSQL, extracts text, chunks,
   embeds, and persists vectors.
5. Retrieval uses server-derived organization and clearance filters before content
   enters an API response or model context.

Upload byte limits, archive/decompression protection, malware scanning, and
instruction-pattern quarantine are not yet implemented.

### 6.3 RAG and agents

1. The user query is bounded by schema.
2. Retrieval returns tenant- and clearance-safe chunks.
3. Application code admits a bounded context and maps authoritative source labels.
4. Bedrock receives the query and admitted context.
5. Strict output schemas and application checks reject unknown evidence, control,
   or tool references.

Prompt instructions are not treated as an authorization boundary. Every generation
path uses a fresh nonce to frame user and retrieved text separately from application
policy and trusted identifiers. Bedrock Guardrail intervention metadata fails closed,
and strict schemas and membership checks reject unknown evidence, control, source,
and tool identifiers. The adversarial corpus exercises authority escalation attempts.
PII classification and minimization remain Phase 8.3 deliverables.

### 6.4 Human approval and execution

1. Reviewer FAIL terminates; PASS creates a trusted bounded recommendation.
2. The approval worker stores a pending approval and an internal task token.
3. A Manager submits approve, reject, or modify without supplying actor, tenant, or
   token authority.
4. The database commits the first decision.
5. Recovery later resolves the token and calls `SendTaskSuccess`.
6. Rejected work terminates without action. Approved/modified work reloads the
   persisted decision, projects the effective action, applies the allowlist, and
   atomically inserts the security review and terminalizes approval execution.

PostgreSQL and Step Functions do not share a transaction. This is at-least-once
recovery with one-shot workflow resume behavior, not distributed exactly once.

## 7. Threat register

Likelihood and impact are qualitative: Low, Medium, or High. **Controlled** means
the required deterministic enforcement exists and has focused tests. **Partial**
means useful controls exist but planned defense-in-depth is missing. **Open** means
the phase must add a control before production use.

| ID | Threat | Likelihood | Impact | Current controls | Status / disposition |
|---|---|---:|---:|---|---|
| CG-01 | Forged, expired, wrong-audience, wrong-issuer, or wrong-use token | Medium | High | Signature, issuer, audience, expiry, not-before, algorithm, key rotation, and `token_use` checks | Controlled; add API Gateway authorizer in Phase 11 without removing application verification. |
| CG-02 | Role escalation through unknown or multiple groups | Medium | High | Exactly one recognized role; ambiguity and missing roles fail closed; central matrix | Controlled; matrix remains a blocking regression. |
| CG-03 | Forged tenant, actor, clearance, approver, or task-token fields | High | High | Immutable `Principal`, `extra="forbid"`, server-derived authority, narrow worker models | Controlled; retain negative-contract tests. |
| CG-04 | Cross-tenant SQL caused by a missing application predicate | Medium | High | Explicit tenant parameters plus forced PostgreSQL RLS, `NOBYPASSRLS` runtime role, and migration `0012` exact table grants | Controlled for current tables; every new tenant table requires real PostgreSQL RLS and privilege tests. |
| CG-05 | Retrieval leaks data above caller clearance or from another tenant | Medium | High | Server-derived filter, vector SQL tenant predicate, RLS, integration tests | Controlled; adversarial cross-tenant corpus remains Phase 8.5. |
| CG-06 | Malicious or oversized document exhausts storage, parser, memory, embedding, or cost | High | High | Short presign TTL, type quarantine, encrypted-PDF quarantine, bounded text chunks | Open: enforce registered/actual size, page/text/chunk quotas, parser timeouts, and upload rate limits in Phase 8.3/8.4. |
| CG-07 | Document or user prompt injection changes model behavior | High | High | Shared nonce-framed isolation, bounded context, Bedrock Guardrail intervention handling, deterministic tools/citations, adversarial corpus | Controlled for identity, tenant, clearance, tool, approval, write, and score authority; adversarial text may still influence non-authoritative prose. |
| CG-08 | Model invokes a write/admin/unknown tool or exceeds its budget | Medium | High | Enum/schema validation, per-agent static allowlists, original `Principal`, bounded budget; Risk/Reviewer have no tools | Controlled for current tools and covered by the shared adversarial corpus. |
| CG-09 | Model invents evidence, source labels, document IDs, or policy IDs | High | High | Application-owned source maps, trusted-ID membership checks, strict output schemas, non-empty explicit control scopes | Controlled, including omitted-scope invented-control and RAG invented-source regressions. |
| CG-10 | Model output becomes authoritative risk or approval state | Medium | High | Deterministic scorer; advisory models; trusted recommendation builder; Manager-only approval | Controlled; any future action type requires a new explicit policy and tests. |
| CG-11 | Unauthorized or altered consequential action executes | Medium | High | Persisted human decision reload, bounded action projector/policy, DB insert trigger, tenant constraints | Controlled for `create_security_review`; arbitrary remediation is out of scope. |
| CG-12 | Task token leaks or is replayed | Medium | High | Token absent from HTTP/domain projections/audit; callback-only IAM; sanitized adapter errors | Partial: field-level encryption and execution-history/log review remain; token is an internal secret capability. |
| CG-13 | Callback succeeds at AWS but delivery bookkeeping remains stale | Low | High | Lease/recovery and one-shot token prevent a second workflow resume | Accepted distributed-transaction limitation; add reconciliation/alerting without claiming exactly once. |
| CG-14 | Execution starts before `callback_delivered_at` is committed | Medium | High | Execution repository fails closed unless `callback_delivered_at` is persisted; `execute_for_workflow` applies a bounded readiness poll (`CallbackDeliveryNotCommittedError`, 4 attempts, 50 ms backoff, rolling back the `FOR UPDATE` row lock between attempts and failing closed with a sanitized `UpstreamError` if rollback fails) before failing closed without executing any side effect | Controlled for bounded callback-commit skew; never marks delivered before provider success and never claims distributed exactly once. |
| CG-15 | Retry or replay duplicates the approved side effect | Medium | High | Deterministic idempotency key, unique constraints, row lock, side effect and terminal state in one transaction | Controlled for the PostgreSQL side effect. |
| CG-16 | PII, secrets, prompt text, or document content leaks through output or logs | High | High | Key-based and structured log redaction, standard-library log privacy filters, bounded sensitive-data detection/minimization for model inputs, output sensitive-data rejection, allowlisted retrieval/RAG metadata, sanitized error projections, and CloudWatch workflow payload exclusion | Controlled for bounded structured patterns and runtime logging; novel/obfuscated PII and external platform log/IAM verification remain residual. |
| CG-17 | Cost or availability denial through login, RAG, workflows, approval API, or ingestion | High | High | Central existing-API peer/user/tenant quotas, conditional shared counter adapter/stack, per-document ingestion budgets, model/embedding/retry budgets, reserved Lambda concurrency, shared tenant worker-step admission (`WorkAdmission` + expiring DynamoDB slot leases), and required local workflow-start admission (`DOCUMENT_READ` + `AI_QUERY` + `WorkAdmission`, which admits one local workflow run rather than each deployed worker step) | Partial: API Lambda stack/authorizer/WAF, queued Step Functions execution caps, API model concurrency, native input-token counting (Nova Pro `CountTokens` unsupported), parser isolation, and live AWS enforcement verification remain open. |
| CG-18 | Browser token theft or OAuth login substitution | Medium | High | `sessionStorage`, one-hour ID token, no implicit grant, server verification, plus RFC 7636 PKCE (`S256`, 48-byte verifier), 24-byte `state` and `nonce` in `cloudguard.auth.pkce`, callback code exchange at `/oauth2/token`, ID token claim validation (`iss`, `aud`, `token_use=id`, `nonce`, `exp`), URL parameter scrubbing, and cancellation/logout lifecycle safety verified by `AuthProvider.test.tsx` | Partial: hosted login PKCE/state/nonce client flow and lifecycle race tests are implemented; static-SPA `sessionStorage` XSS exposure, live Cognito Hosted UI runtime verification, and edge CSP remain open. |
| CG-19 | Overbroad AWS access or infrastructure data exposure | Medium | High | Per-Lambda roles, scoped model/cluster/secret ARNs, blocked S3 public access, TLS | Partial: audit start-execution rights, logging, encryption keys, and target API stack in Phase 8/11. `SendTaskSuccess` necessarily uses `*`. |
| CG-20 | Audit alteration, deletion, or inconsistent dual writes | Low | High | PostgreSQL trigger-generated append-only approval ledger, RLS/FORCE RLS, privilege revocation | Controlled for approval audit. PostgreSQL remains canonical; any future Dynamo projection must use an outbox. |
| CG-21 | Vulnerable dependency or leaked repository secret reaches a build | Medium | High | Ruff security rules, blocking `gitleaks`, blocking `pip-audit` (`backend` and `infrastructure`), blocking `npm audit --audit-level=high`, executable frontend tests (`npm test`), and blocking `aquasecurity/trivy-action@57a97c7e7821a5776cebc9bb87c984fa69cba8f1` (`v0.35.0`) container image scanning without `ignore-unfixed: true` (using a component-scoped, time-bounded `.trivyignore.yaml` targeting `usr/local/bin/aws-lambda-rie`) | Controlled in repository CI workflow; live branch-protection enforcement depends on GitHub repository settings. |
| CG-22 | Attacker-controlled request ID pollutes or expands logs | Medium | Medium | Phase 8.1 accepts only a 1-128 character safe alphabet and generates a server ID otherwise | Controlled with focused API tests. |
| CG-23 | Runtime database role is over-privileged or unusable after a clean rebuild | Medium | High | Migration `0012` resets direct/PUBLIC grants across current tables, grants only repository operations, withholds destructive/DDL-like privileges, and keeps the audit sequence inaccessible; CI creates the role before migration and never blanket-grants tables | Controlled with static and real-role regressions. New tables require an explicit privilege decision. |

## 8. Authorization and segregation of duties

The central source of truth is `backend/app/security/authz.py`. The exhaustive
unit matrix and API route matrix fail when a new permission or protected route is
not classified. The human-readable mirror is
[`security/AUTHORIZATION_MATRIX.md`](security/AUTHORIZATION_MATRIX.md).

Important non-inheritance rules:

- Admin does not implicitly receive every permission.
- Only Manager can decide approvals or override risk severity.
- Admin can read approvals but cannot decide them.
- Analyst and Manager can submit RAG queries; Admin can read documents but cannot
  send restricted content to the LLM through the RAG endpoint.
- Retrieval visibility and action permissions are distinct from confidentiality
  clearance.

## 9. Security decisions from the Phase 8.1 review

### PostgreSQL approval audit remains canonical

Phase 7 implemented approval audit events in the same PostgreSQL transaction and
trigger boundary as approval state. Replacing that with, or directly dual-writing
to, DynamoDB would weaken consistency. PostgreSQL remains the canonical approval
audit ledger. A later read-optimized projection is allowed only through a durable
outbox/replay design. See
[ADR-0017](adr/0017-postgresql-approval-audit-canonical.md).

### API baseline is enforced before model hardening

Phase 8.1 removes the retired development-principal CORS header, validates exact
origins, requires HTTPS origins in production, bounds reflected request IDs, and
requires both document-read and AI-query permission for RAG. These controls reduce
ambient authority without depending on a model or external service.

### Prompt isolation is defense in depth

Phase 8.2 applies one provider-neutral framing policy to RAG and every agent model
call. A fresh unpredictable nonce separates the user request and each retrieved or
upstream-model section from application-authored policy and trusted identifiers.
Configured Bedrock Guardrail intervention signals are rejected without returning
generated content. Strict schemas, application-owned allowlists, identifier
membership checks, deterministic scoring, and human authorization remain the
enforcement boundary. See
[`security/PROMPT_INJECTION_DEFENSES.md`](security/PROMPT_INJECTION_DEFENSES.md).

### Callback ordering is preserved with bounded readiness polling

The execution boundary never marks `callback_delivered_at` before provider success (`SendTaskSuccess`). That would hide failed callbacks and violate durable ordering. To handle the narrow window where Step Functions invokes the approved-action worker before the callback worker's PostgreSQL commit of `callback_delivered_at` becomes visible, `AuroraDataAPIApprovalExecutionRepository.execute_for_workflow` rolls back its read transaction (releasing the `FOR UPDATE` row lock), requires confirmed rollback before attempting another poll (failing closed with a sanitized `UpstreamError` if rollback fails), and performs a bounded readiness retry (`CallbackDeliveryNotCommittedError`, 4 attempts, 50 ms backoff) before failing closed without executing any side effect. This remains at-least-once recovery with one-shot workflow resume and idempotent side-effect execution, not distributed exactly once.

## 10. Known residual risks

- XSS can steal the SPA token because static hosting cannot issue an httpOnly
  application session cookie.
- Authorized text sent to Bedrock crosses the application/provider boundary; PII
  classification and minimization are not complete.
- A sufficiently effective prompt injection may influence generated prose even when
  deterministic tools and authority remain protected.
- Task tokens are stored in the tenant-protected approval row without application-
  level field encryption.
- PostgreSQL and Step Functions cannot provide one distributed transaction.
- The current repo does not enforce public-edge rate limits, WAF policy, or upload
  resource quotas.
- S3 and state-machine encryption use AWS-managed rather than customer-managed keys.
- Cognito threat protection is audit-only and MFA is optional.
- No malware scanning or sandboxed document parser exists yet.

These are not reasons to weaken existing behavior or to claim a stronger guarantee.
They define the next controls and the honest production-readiness boundary.

## 11. Out of scope

- arbitrary autonomous remediation;
- model-issued approvals or identity;
- emergent agent delegation;
- a claim of distributed exactly-once delivery;
- organization-wide SIEM/SOC procedures;
- physical security and AWS-managed-service internals; and
- full compliance certification or penetration-test attestation.

## 12. Review triggers

Review and version this document when any of the following changes:

- identity provider, token type, role, permission, or clearance mapping;
- tenant schema, RLS policy, runtime database role, or data store;
- model provider, system prompt, context format, tool, or agent graph;
- approval action type, callback semantics, or execution side effect;
- public route, upload format/limit, rate-limit policy, or deployment topology;
- logging/audit fields or retention; or
- a vulnerability, adversarial test, or incident invalidates an assumption.
