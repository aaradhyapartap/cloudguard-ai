# ADR 0018: shared single-region API admission counters

Status: implemented for review in Phase 8.4b; deployment verification outstanding.

## Context

Per-process counters cannot enforce a shared API budget across Lambda instances.
Lambda remains outside the database VPC; adding Redis/NAT or an asyncpg limiter
would change that architecture. Approval audit remains canonical in PostgreSQL.

## Decision

Use one centralized fixed-window policy and a provider-neutral RateLimitStore.
Bounded, locked process memory is selected only in the local environment.
Every non-local API requires a separate DynamoDB table and uses conditional
UpdateItem counters. No fallback admits traffic after a shared-store failure.

Charge a peer authentication/login quota, then verified user and tenant route
budgets before identity provisioning or route work. Preserve RBAC and all domain
authorization. Return 429 with Retry-After for quota denial and 503 for store
unavailability. Counter keys use hashed framed subjects and time-specific keys.
TTL is cleanup, never the quota reset mechanism.

## Consequences

This introduces a distinct mutable DynamoDB operational store, not approval audit
storage or an audit dual write. Conditional updates need only table-scoped
UpdateItem. On-demand writes still cost money, including denied conditional
requests; edge protection remains required. Sequential debits and uncertain
provider outcomes may conservatively charge requests that never reach services.
Fixed windows allow boundary bursts, clocks must be synchronized, and this design
does not provide a distributed semaphore or multi-region linearizable quota.

The counter stack exists, but no API Lambda construct exists here to wire its IAM
grant and environment. That wiring and live shared-instance validation remain
required. No new HTTP workflow-start endpoint or VPC networking is introduced.
