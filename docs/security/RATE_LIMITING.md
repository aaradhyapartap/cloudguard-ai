# Centralized API request admission

Phase 8.4b defines policy in `app/security/rate_limits.py`, storage semantics in
`app/ports/rate_limit_store.py`, and request integration in `app/api/deps.py`.
Every existing registered API route inherits the pre-authentication dependency.
Protected routes also use cached principal resolution, which applies verified
user and tenant quotas before identity database provisioning or route services.
All existing RBAC, organization provisioning, clearance, RLS, and approval checks
still run after admission. A quota grant is not authorization.

## Fixed-window policy

All windows are 60 seconds, aligned to UTC epoch minutes. A tenant bucket has five
times its corresponding user budget. It is shared across that tenant's users.

| Bucket | Requests/window | Scope |
| --- | ---: | --- |
| Authentication attempts on protected routes | 300 | ASGI peer |
| Local login, including rejected roster lookups | 10 | ASGI peer |
| Public authentication configuration | 120 | ASGI peer |
| General reads/system routes | 120 | Verified user and tenant |
| Document registration/completion | 30 | Verified user and tenant |
| Retrieval | 60 | Verified user and tenant |
| RAG | 20 | Verified user and tenant |
| Compliance reads | 120 | Verified user and tenant |
| Compliance mutations/computation | 30 | Verified user and tenant |
| Model candidate extraction | 10 | Verified user and tenant |
| Approval reads | 120 | Verified user and tenant |
| Approval decisions | 30 | Verified user and tenant |

Liveness/readiness are explicitly exempt. OpenAPI/docs are outside this API router
and remain disabled by default in production. Nonexistent routes and malformed
JSON rejected by the framework before dependency resolution do not consume these
application quotas. Edge/body/parser limits remain necessary.

Counters use policy category, window length, window number, and a SHA-256 digest of
a JSON-framed subject tuple. They never store JWTs, raw user/tenant identifiers,
email, raw paths, resource IDs, request bodies, or query strings. Hashing is data
minimization, not cryptographic anonymization of low-entropy values such as IPs.
Changing resource IDs, queries, tokens for the same subject, organization headers,
or X-Forwarded-For headers does not create a new subject budget.

The peer comes only from `request.client`. Configure the ASGI adapter/server to
trust forwarding metadata only from the actual edge; do not enable arbitrary
forwarded-header trust. If peer identity is unavailable, requests share a fixed
unknown-peer bucket. A NAT or incorrectly configured proxy can share a quota among
many legitimate callers. Cognito Hosted UI login is not this API's local login
route and needs its own provider/edge protection.

## Atomicity and failure behavior

Local counters use a lock and at most 10,000 live keys per application instance.
Expired keys are reclaimed. Capacity exhaustion denies new keys rather than
allocating unbounded memory. These counters reset on process restart and are
intentionally not a distributed deployment control.

Non-local API composition requires `RATE_LIMIT_TABLE_NAME`; missing configuration
fails application construction. Its DynamoDB adapter uses a single conditional
UpdateItem per bucket, combining a count increment with a less-than-limit
condition. It performs no read-before-write and no scans. All instances must use
the same table, region, policy version, and synchronized clock. Global-table or
active-active multi-region enforcement is not supported by this algorithm.

The window is part of the physical key. TTL is cleanup only: delayed deletion of
old rows cannot grant extra requests in the current window. Expiry is numeric
epoch seconds in `expires_at`. Near a window boundary, up to twice the nominal
limit can be admitted over a short interval; this is not a sliding-window or
strict instantaneous/concurrency limit. Clock skew can also affect boundaries.

The pre-authentication, user, and tenant debits are sequential, not a transaction.
Earlier debits are not refunded when a later quota, authorization check, body
validation, or service call fails. This is conservative and can deny earlier than
the nominal successful-request count. SDK automatic retries are disabled for
non-idempotent increments; provider timeout outcomes can still be uncertain.
Any store failure fails closed with a static 503 response and no route work.
Quota denial returns structured RATE_LIMITED/429 with a positive integer
Retry-After, request correlation, and existing security headers. Retry-After is
CORS-exposed. No counters or provider error strings are returned to clients.

## Deployment and remaining work

`RateLimitStack` provisions one retained, deletion-protected, on-demand,
AWS-managed-encrypted table with TTL. It is separate from PostgreSQL approval
audit, and is not an audit projection or dual write. Its `grant_admission` helper
grants only dynamodb:UpdateItem on that table, restricted to `api:v1:*` keys.
Phase 8.4d also uses this operational table for worker quotas and expiring slots,
with disjoint namespaces and scoped worker grants. See
`WORKER_ADMISSION.md` for rollout and failure semantics.

The repository does not yet define an API Lambda stack or workflow-start HTTP
route. Therefore the new table is NOT automatically connected to an API role.
Before a non-local API can serve traffic, the owning deployment must inject the
table name and grant the exact API execution role table-scoped UpdateItem (using
the helper or its equivalent). Do not grant approval workers general table access
or weaken their existing policies. Never fix an unavailable limiter by falling
back to memory. No deployment or external IAM mutation is performed by this patch.

Also required before production readiness:

- verify API peer identity, actual IAM/environment wiring, failure modes, and
  concurrent admission across real Lambda instances;
- edge/API Gateway/WAF controls for volumetric abuse, counter-store costs,
  high-cardinality peer attacks, and pre-dependency parser/body work;
- workflow-start admission, queued-workflow limits, and API model concurrency;
- live verification of worker admission and model/retry budgets implemented in source;
- storage-side upload limits and parser CPU/decompression isolation; and
- Python 3.12 CI and full PostgreSQL-backed regressions.

Tests use controlled clients and clocks, not live AWS enforcement. This slice
does not claim Phase 8.4 or Phase 8 is complete.
