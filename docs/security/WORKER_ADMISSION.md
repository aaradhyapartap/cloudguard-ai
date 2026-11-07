# Tenant worker admission

Phase 8.4d gates the four cost-bearing deployed worker entry points using
`WorkAdmission`, ports, and local/DynamoDB adapters. This extends resource budgets;
it does not create a workflow-start API or complete Phase 8.4.

## Policy and placement

| Scope, shared by one tenant | Fixed UTC minute | Active worker slots |
| --- | --- | --- |
| Research, Risk, and Reviewer combined | 60 admitted steps | 2 |
| Document ingestion | 10 admitted steps | 1 |

Each model slot covers the complete worker step, including its bounded retrieval
and generation calls. Ingestion admission precedes repository construction and
the document processing claim, persistence, embedding, and external events.
The local document worker also gates before opening its tenant database session.
Rejected ingestion has not claimed the document and can remain QUEUED.

Organization scope comes from the existing typed Principal in trusted workflow
state, or the normalized ingestion task's organization UUID. Evidence, model
output, and question text never select the quota scope. Admission is not
authentication or authorization: trusted invocation IAM, authenticated workflow
provenance, RBAC, tenant filtering, and PostgreSQL RLS remain essential.

These are active **step** limits, not per-tenant workflow counts. Queued
StartExecution requests, idle executions, and pending human approvals hold no
slot. Approval creation, callback recovery, and approved-action execution receive
no admission-table permissions. Human approval semantics and the ingestion DLQ
are unchanged. API RAG/extraction concurrency is not covered by this worker gate.

## Storage and IAM

The existing RateLimitStack table remains operational admission state, not audit
storage. No PostgreSQL migration, audit projection, or audit dual write is added.
The table is single-region, retained, encrypted, deletion-protected, and has TTL.

| Key namespace | API permission | Cost-bearing worker permission |
| --- | --- | --- |
| `api:v1:*` | UpdateItem | None |
| `worker:v1:*` | None | UpdateItem |
| `slot:v1:*` | None | PutItem, DeleteItem |

Grants use the exact table ARN and ForAllValues:StringLike/LeadingKeys conditions.
No GetItem, Query, Scan, or table-wide data permission is added. Slots store a
hashed organization identifier, work kind, fixed slot index, random owner nonce,
and expiry. Counters hash their subject. Hashes are pseudonymous, not anonymous.
The worker role can address all tenants in its permitted namespaces; application
identity and condition expressions remain trusted, not enforced by tenant IAM.

CDK wires RATE_LIMIT_TABLE_NAME and these grants to the four cost-bearing workers.
Non-local composition rejects a missing table; it never falls back to local
memory. Local stores are bounded, process-local test/development substitutes.
The repository still has no API Lambda stack: API environment/IAM wiring remains
the owning deployment's responsibility.

## Lease and failure semantics

A conditional PutItem acquires an absent or expired fixed slot. The 600-second
lease exceeds the current 120-second model and 300-second ingestion Lambda
timeouts, with a regression checking every cost-bearing function's timeout.
Expiry is compared explicitly; DynamoDB TTL deletion is not the expiry authority.
Release uses conditional DeleteItem with the exact owner nonce. An old invocation
cannot delete a newer invocation's slot after expiry and reacquisition.

Acquire the slot first, debit the worker minute quota second, then run work.
Concurrency refusal does not debit the minute quota. Quota refusal/store failure
releases the acquired slot before propagating. Successful work and body failures
both attempt cleanup. Debits are not refunded after work failures, so retries
consume budget too. Fixed-window boundaries can admit twice the nominal minute
budget in a short interval; this is not a sliding-window limit.

Acquisition and counter-store failures fail closed. SDK retries are disabled;
connection/read timeouts are two seconds. A timed-out write may have succeeded,
conservatively retaining capacity until expiry. Cleanup failure emits only a
static event, work kind, and exception type. It does not convert already completed
work into a failure that could trigger a replay. Process death or cancellation
may also leave capacity held until expiry. Admission refusal follows existing
sanitized worker failure behavior: no new automatic busy/quota retry is added.
Existing ingestion failure/DLQ handling is retained; operators must reconcile
rejected work through the established recovery process.

This lease is not a fencing lock or exactly-once guarantee. Synchronized clocks,
bounded execution time, unchanged timeouts, and trusted clients are assumptions.
Remote provider work can outlive a Lambda timeout. IAM/runtime validation and
provider-side cost controls are still required; the gate is not a hard spend cap.

## Rollout and validation

API counter keys change from `v1:*` to `api:v1:*` to isolate permissions. This
creates fresh counters for the current window. Deploy API code and its new IAM
condition together: mixed old/new versions can reset budgets or fail closed.
Old rows expire naturally. Provision the table and worker grants/environment
before enabling new worker code. Do not broaden permissions or switch to memory
to bypass an unavailable limiter. No deployment is performed by this patch.

Deterministic tests cover shared-client competing instances, separate tenant/kind
scopes, temporal quotas, expiry without TTL deletion, stale-owner release,
outages, denial before costly worker work, required non-local configuration, and
synthesized worker/API IAM. Controlled clients are not live DynamoDB verification.
Full backend/PostgreSQL, Ruff, mypy, infrastructure tests, and Python 3.12 CI must
pass before claiming this slice validated. Live AWS concurrent admission,
conditional failure behavior, IAM evaluation, timeout/crash recovery, and cleanup
outage behavior remain deployment checks.
