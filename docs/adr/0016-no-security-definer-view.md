# ADR-0016: Local login reads a code fixture, not the database

- **Status:** accepted
- **Date:** 2026-08-23
- **Phase:** 2

## Context

The local login endpoint needs to find a user by email. `users` is under
Row-Level Security, and RLS needs a tenant context — but establishing the tenant
context is what logging in is *for*. Circular.

## What was tried and why it failed

A `SECURITY DEFINER` view (`security_invoker = false`), so the lookup would run
as the view's owner rather than the caller.

**It returns zero rows.** Migration 0001 uses `FORCE ROW LEVEL SECURITY`, which
applies policies to the table owner as well. The view runs as its owner, the
owner is still subject to the policy, nothing comes back. Confirmed against real
PostgreSQL — `SELECT count(*) FROM dev_user_directory` returned 0 while the rows
plainly existed.

Worth keeping: `FORCE` is the keyword that makes RLS a real control rather than
a suggestion, and it does not carve out an exception for clever plumbing.

## Options

| Option | Verdict |
|---|---|
| `SECURITY DEFINER` view | Does not work under `FORCE RLS` |
| A second DB role with `BYPASSRLS` | Works. Correct for genuine cross-tenant administration; disproportionate for a dev convenience — and a bypass role that exists is a bypass role that gets used for something else |
| Drop `FORCE`, keep `ENABLE` | Removes the protection to enable a dev feature. No |
| A roster in application code | No database, no bypass, no migration |

## Decision

`app/adapters/local/directory.py` holds the local roster. `/auth/dev-login`
reads it; `scripts/seed_data.py` imports the same list, so the seeded rows and
the accepted logins describe the same people by construction.

Migration 0003 (the view) was deleted rather than kept as dead schema.

## Consequences

No `SECURITY DEFINER` object exists in a product whose entire premise is
auditable access control — which is worth something on its own when someone
reads the schema.

Production is unaffected: Cognito holds its own user directory and never queries
this table to authenticate.

The cost is that adding a local user means editing Python and re-running the
seed script. For a fixed four-account development roster, that is the right
amount of friction.

## Revisit when

A genuine cross-tenant administrative feature is needed — bulk user
administration, a data-migration tool, an operator console. At that point
introduce a `BYPASSRLS` role deliberately, with audit logging on its use.
