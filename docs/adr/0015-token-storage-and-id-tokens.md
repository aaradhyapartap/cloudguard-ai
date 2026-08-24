# ADR-0015: ID tokens as API credentials, held in sessionStorage

- **Status:** accepted, with known compromises
- **Date:** 2026-08-23
- **Phase:** 2

Two decisions recorded together because they share one root cause: ADR-0012
committed to a static SPA with no server compute, and both consequences flow
from that.

## Part 1 — ID token rather than access token

**Context.** Every query in the system is scoped by `organization_id`, which
arrives as `custom:organization_id`. Cognito access tokens do not carry custom
attributes; ID tokens do.

**Options.** Verify the ID token and accept it as an API credential; or add a
pre-token-generation Lambda that copies custom attributes into the access token;
or store the mapping server-side and look it up by `sub` on every request.

**Decision.** Verify the ID token. `token_use` is checked explicitly — an access
token from the same pool, with a valid signature, correct issuer and correct
audience, is rejected.

**Consequences.** ID tokens are specified as being *about* a user, for the
client, not as bearer credentials *for* an API. Using them this way is a
recognised compromise. It works because the API validates audience against its
own app client, so a token minted for a different client will not pass.

The hardening path is the pre-token-generation Lambda. It is Phase 8 work,
deliberately deferred: it adds a Lambda to the auth hot path for a benefit that
is real but not urgent at demo scale.

**Revisit when** a second API client exists, or the auth path is hardened in
Phase 8 — whichever comes first.

## Part 2 — Token storage in the browser

**Context.** A static export has no server session, so no httpOnly cookie. The
token has to live somewhere the browser can reach, and everywhere the browser
can reach, injected script can reach too.

**Options.**

| Where | Survives refresh | Survives tab close | Readable by XSS |
|---|---|---|---|
| In memory only | ✗ | ✗ | during the session |
| `sessionStorage` | ✓ | ✗ | ✓ |
| `localStorage` | ✓ | ✓ | ✓ |
| httpOnly cookie | ✓ | ✓ | ✗ — needs a server |

**Decision.** `sessionStorage`, with a one-hour token lifetime.

**Consequences.** This is the honest version: **an XSS bug in this application
is a session-theft bug.** No storage choice available to a static SPA changes
that; `sessionStorage` only shortens the window by dying with the tab.

What actually reduces the risk: a restrictive CSP, no `dangerouslySetInnerHTML`
anywhere in the codebase, one-hour tokens, and Cognito token revocation enabled
so a stolen refresh token can be killed.

This belongs in `SECURITY.md` under known limitations, not buried. A threat
model that lists no residual risk is a threat model nobody believes.

**Revisit when** session security needs to be stronger than the application's
XSS posture. That means a token-exchange endpoint issuing an httpOnly cookie,
which means giving up the static export and reopening ADR-0012.
