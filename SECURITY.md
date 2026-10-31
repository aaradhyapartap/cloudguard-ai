# CloudGuard AI Security Policy

CloudGuard AI handles tenant-scoped compliance documents, model context, risk
records, and human approval decisions. Security controls are part of the product
contract, not optional deployment guidance.

This document describes the repository at the start of Phase 8. It distinguishes
controls that are enforced in code from controls still scheduled for later Phase 8
work. The detailed analysis is in [the threat model](docs/THREAT_MODEL.md), and the
current role and route grants are in the
[authorization matrix](docs/security/AUTHORIZATION_MATRIX.md).

## Reporting a vulnerability

Do not open a public issue containing an exploit, secret, task token, tenant data,
or other sensitive evidence.

Use GitHub's private vulnerability-reporting or security-advisory channel for this
repository when it is available. Otherwise, contact the repository owner privately
through the contact method on their GitHub profile. Include:

- the affected commit and component;
- reproduction steps using synthetic data;
- the expected and observed security boundary;
- likely impact and prerequisites; and
- a minimal remediation suggestion, if known.

Never include live AWS credentials, production tokens, real personal data, or full
customer documents. Revoke or rotate any credential that may have been exposed
before sending the report.

## Supported versions

CloudGuard AI is under active portfolio development. Security fixes are made on the
current development branch and are not backported to historical phase branches.
Only the latest published commit is supported.

## Non-negotiable security invariants

Changes must preserve all of these properties:

1. The verified human `Principal` is authoritative for user, organization, role,
   department, and clearance.
2. Request bodies, retrieved documents, prompts, and model output cannot supply or
   override identity or tenant authority.
3. Tenant access is constrained in application queries and by PostgreSQL Row-Level
   Security. The application role must remain `NOBYPASSRLS`.
4. Confidentiality clearance is derived from the authenticated role and applied
   before retrieval. It is not a client-controlled display filter.
5. Services depend on ports. AWS SDK use remains inside adapters.
6. Tool access is checked by application-owned allowlists and the original human
   `Principal`; the model can only propose bounded intent.
7. Deterministic Python scoring remains authoritative. Model estimates are advisory.
8. Consequential Phase 7 actions require a persisted Manager decision. Analyst,
   Admin, model, and workflow identities cannot decide an approval.
9. The approved-action worker reloads persisted approval state and never accepts
   action authority from workflow input.
10. Step Functions task tokens remain internal capabilities and must not enter HTTP
    contracts, public projections, audit payloads, or logs.
11. Published migrations are immutable. Security corrections use forward migrations.
12. Security controls fail closed; tests are never fixed by weakening an invariant.

## Controls enforced in the current source

| Area | Enforced control |
|---|---|
| Authentication | Cognito RS256 or local-only HS256 tokens are checked for signature, issuer, audience, expiry, and `token_use`; ambiguous roles fail closed. |
| Authorization | A central permission matrix is applied by FastAPI dependencies and exhaustively tested across roles and protected routes. |
| Tenant isolation | The signed organization claim becomes an immutable `Principal`; repositories use tenant predicates; tenant tables use enabled and forced RLS. |
| Confidentiality | Retrieval levels are calculated from the server-side role-to-clearance mapping. |
| RAG | Context is bounded, retrieved text is labeled untrusted, zero-evidence requests skip generation, and response sources come from admitted retrieval records rather than model-provided identifiers. |
| Agents | The graph and tool allowlists are application-owned; tool budgets are bounded; Risk and Reviewer have no tools; invented evidence references fail closed. |
| Model boundary | Every generation path uses shared nonce-framed separation of user/retrieved text from application policy and trusted identifiers; configured Bedrock Guardrail interventions and unknown identifier/tool references fail closed. |
| Risk | Authoritative compliance scoring is deterministic and stored with immutable snapshots; model-produced estimates cannot overwrite it. |
| Approval | Manager-only decisions, immutable decision state, internal task-token recovery, bounded action policy, and transactional side-effect idempotency are enforced. |
| Audit | Approval audit events are PostgreSQL-backed, append-only, trigger-generated, tenant-scoped, and protected by RLS and least-privilege grants. |
| API baseline | Exact CORS origins, production HTTPS origins, bounded request identifiers, restrictive API response headers, and production OpenAPI-off defaults are enforced. |
| Infrastructure | Current Lambda IAM statements are action- and resource-scoped except `states:SendTaskSuccess`, whose API does not support resource-level task-token scoping. S3 public access is blocked and SSL is required. |

These controls do not imply that every item in the Phase 0 target architecture is
already implemented. In particular, the repository does not yet provision an API
Gateway authorizer, WAF, Bedrock Guardrail resource, customer-managed KMS keys,
or a DynamoDB audit subsystem.

## Deployment security baseline

Before exposing a non-local environment:

- set `ENVIRONMENT` to `dev` or `prod` as appropriate;
- use `IDENTITY_PROVIDER=cognito`; local identity is rejected outside `local`;
- use exact HTTPS `CORS_ORIGINS`; wildcard origins are rejected;
- disable OpenAPI in production;
- use `cloudguard_app` or an equivalent `NOSUPERUSER NOBYPASSRLS` runtime role;
- apply migration `0012` or later so Alembic owns exact runtime table grants and
  the application has no blanket delete, truncate, reference, trigger, or audit
  sequence privilege;
- obtain AWS credentials only through workload roles;
- inject database credentials through Secrets Manager;
- restrict `states:StartExecution` to the trusted application entry point;
- restrict S3 writes to application-issued document keys;
- retain encryption, versioning, public-access blocking, and TLS enforcement;
- configure log access and retention as sensitive operational data;
- provision a Bedrock Guardrail and configure both `BEDROCK_GUARDRAIL_ID` and
  `BEDROCK_GUARDRAIL_VERSION`; production startup rejects missing or partial values;
- enable bounded rate limits and abuse controls before public traffic once Phase 8.4 lands; and
- run the Phase 8 security regression suite and blocking dependency/secret scans.

## Secrets and sensitive data

Never commit or log:

- AWS keys, database passwords, private keys, or bearer tokens;
- Step Functions task tokens;
- presigned S3 URLs;
- raw request authorization headers;
- raw document bodies, retrieved context, embeddings, or model prompts; or
- real PII in fixtures, issue reports, screenshots, or test output.

Structured logging includes key-based redaction, but key-name redaction alone is not
a proof that arbitrary exception text is safe. Until Phase 8.3 completes the logging
audit, new logs must use bounded identifiers, event categories, and exception type
names instead of raw exception strings.

## Security validation

Before a security-related commit, run the focused tests for the changed boundary,
then the relevant regression suites, Ruff, mypy, infrastructure tests when CDK
changes, and `git diff --check`. Database security changes also require PostgreSQL
integration testing through the `cloudguard_app` role.

Do not stage or publish if:

- an authorization or cross-tenant test fails;
- a secret/token scan finds a real leak;
- an old migration changed unexpectedly;
- a model or workflow payload became authoritative for identity or action; or
- a known security limitation was hidden instead of resolved or documented.

## Known limitations and scheduled hardening

The following are open and must not be described as implemented:

- Nonce-delimited prompt isolation, a reusable adversarial injection corpus,
  Guardrail intervention handling, and deterministic identifier/tool validation are
  implemented. These controls protect application authority but cannot guarantee
  that adversarial text never influences generated prose. The current CDK source
  does not provision the Guardrail resource; deployment must supply its ID/version.
- Bounded sensitive-data pattern detection minimizes model-facing generation and
  embedding inputs and rejects detected sensitive model output. This is not complete
  PII recognition; names, unusual formats, obfuscation, and unlabelled secrets can
  escape detection. See [model data handling](docs/security/DATA_HANDLING.md).
  Public metadata/evidence projections, platform logs, and workflow-history review
  remain Phase 8.3 work.
- Endpoint rate limiting, upload quotas, extraction limits, and WAF/API Gateway abuse
  controls remain Phase 8.4 work.
- The static SPA stores its ID token in `sessionStorage`; XSS can therefore steal a
  session. The hosted-login implementation still requires complete PKCE, state,
  nonce, and callback handling before it is production-ready.
- Application JWT verification is implemented, but the current CDK source does not
  yet provision the target API Gateway JWT authorizer.
- Callback delivery is not distributed exactly once. A provider-success/database-
  bookkeeping crash window and a callback-delivered/execution-start race remain.
- Approval task tokens are application-secret and database-protected but are not yet
  encrypted at the application field level.
- Approval audit is canonical in PostgreSQL; a DynamoDB audit projection does not
  currently exist.
- S3 and Step Functions currently use AWS-managed encryption rather than the target
  customer-managed KMS design.
- Historical migration `0005` may leave indexes absent in databases that applied an
  earlier published version; this must be audited with a forward migration if needed.
- The prompt-injection corpus is a bounded regression set, not a substitute for
  ongoing adversarial evaluation as prompts, models, or tools change.

See the threat register for ownership, severity, and planned disposition.
