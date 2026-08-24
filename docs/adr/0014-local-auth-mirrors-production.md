# ADR-0014: Local development uses real signed tokens

- **Status:** accepted
- **Date:** 2026-08-23
- **Phase:** 2

## Context

Phase 1 authenticated with a plaintext `x-dev-principal` header containing JSON.
It was fast to build and it skipped every part of the mechanism it stood in for:
no signature, no issuer check, no audience check, no expiry, no group-to-role
mapping. The first time any of that ran was going to be against real Cognito, in
AWS, where debugging is slowest.

Cognito has no local emulator, so some local mechanism is required.

## Options

| Option | Pros | Cons |
|---|---|---|
| Keep the plaintext header | Trivial | Local development exercises none of the auth code; bugs surface only in AWS |
| Mock the verifier | Tests the mapping | Still skips signature, issuer, audience, expiry |
| Sign real tokens locally with HS256 | Exercises the entire pipeline offline and free | A second adapter to maintain; a dev signing key exists |
| Run against a real dev user pool always | Maximum fidelity | Needs network and AWS for every local run; slow feedback |

## Decision

An `IdentityProvider` port with two adapters. `CognitoIdentityProvider` verifies
RS256 against the pool's JWKS; `LocalIdentityProvider` verifies HS256 against a
development secret. Both emit identical claim names — `cognito:groups`,
`custom:organization_id`, `custom:department`, `token_use` — and both hand off
to the same `claims_to_principal` mapping.

The `x-dev-principal` header is deleted, not deprecated.

## Consequences

Local development runs the real path: bearer parsing, signature verification,
issuer and audience checks, expiry, ambiguity handling, principal construction.
A test suite of 149 covers it without an AWS account.

The cost is a dev signing key that exists on disk. Three guards, each with a
test: `Settings` refuses `identity_provider="local"` outside the local
environment, the secret must be at least 32 bytes (RFC 7518), and
`/auth/dev-login` is *absent from the routing table* elsewhere rather than
guarded inside the handler.

The residual risk is claim-name drift — if the two adapters stop agreeing, local
work stops testing what runs in AWS. `test_claim_names_match_cognito_exactly`
exists to catch that.

## Revisit when

Cognito gains a local emulator, or a second real identity provider is added and
the "local" adapter becomes redundant.
