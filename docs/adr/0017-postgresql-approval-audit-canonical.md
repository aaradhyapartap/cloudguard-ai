# ADR-0017: PostgreSQL is canonical for approval audit events

- **Status:** accepted
- **Date:** 2026-09-07
- **Phase:** 8

## Context

The Phase 0 target architecture proposed PostgreSQL as the transactional system of
record and DynamoDB for a general append-only audit stream. By Phase 7, the actual
implemented approval lifecycle already required atomic coupling between approval
state, bounded action execution, and audit emission.

Migration `0011` therefore introduced `approval_audit_events` in PostgreSQL. Events
are emitted by database triggers from the authoritative approval transition, are
append-only, are protected by RLS/FORCE RLS, and cannot be directly inserted,
updated, or deleted by the application runtime role.

Phase 8 must decide whether to keep that implementation or add DynamoDB without
creating a dual-write inconsistency.

## Options

| Option | Benefits | Costs and risks |
|---|---|---|
| Replace PostgreSQL audit with DynamoDB | Matches the original target diagram; independent scaling | Breaks atomic association with approval transactions; migration and reconciliation complexity |
| Write PostgreSQL and DynamoDB directly | Immediate query copies in both stores | No shared transaction; one write can succeed while the other fails |
| Keep PostgreSQL canonical | Preserves current transactional integrity, RLS, and trigger-enforced immutability | General audit scale and cross-service querying remain tied to PostgreSQL |
| PostgreSQL canonical plus later outbox projection | Preserves authority while enabling a read-optimized copy | Requires outbox publication, replay, deduplication, and lag monitoring |

## Decision

PostgreSQL remains the canonical ledger for approval audit events.

CloudGuard AI will not introduce a direct PostgreSQL/DynamoDB dual write. If a
DynamoDB or external audit projection becomes useful for operations, analytics, or
retention, it must be derived through a durable transactional outbox (or equivalent
replayable change stream) whose source event is committed with the approval state.
The projection is non-authoritative and must tolerate duplicate delivery.

This decision is limited to the implemented approval audit ledger. It does not claim
that a general `AuditEvent` or `AgentExecution` persistence subsystem exists.

## Consequences

- Approval state and its audit event cannot diverge because of an application-level
  dual-write failure.
- Existing Phase 7 constraints, triggers, RLS, and least-privilege grants remain the
  security boundary.
- The Phase 0 diagram and README must describe DynamoDB as target or optional
  projection architecture rather than current implementation.
- A future projection needs an outbox schema, publisher, deterministic event ID,
  replay procedure, lag metric, and reconciliation test.
- PostgreSQL backup, retention, access logging, and restore testing apply to both the
  transactional record and the canonical approval audit history.

## Revisit when

- approval audit volume or access patterns materially exceed PostgreSQL needs;
- Phase 10 observability requires a cross-service immutable stream;
- Phase 11 deployment introduces a supported outbox/change-data-capture mechanism;
  or
- retention or regulatory requirements demand independently administered storage.
