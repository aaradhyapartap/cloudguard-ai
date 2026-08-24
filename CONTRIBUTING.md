# Contributing

## Setup

```bash
./scripts/local_setup.sh
```

## Before every commit

```bash
make check     # lint + typecheck + tests
```

## Architectural rules

These are enforced mechanically, not by review:

1. **`app/services/` must not import `boto3`.** Ruff's banned-api rule fails the
   build. Cloud SDKs live in `app/adapters/` behind a port.
2. **Every repository takes a `Principal`.** There is no constructor without
   one, so there is no way to query across tenants by accident.
3. **Model IDs are configuration.** No literal model identifier in application
   code — it belongs in `BedrockSettings`.
4. **New permissions require updating `tests/unit/test_authz.py`.** The test
   asserts the whole matrix, so an unspecified grant fails the build.

## Decisions

Anything that changes structure gets an ADR in `docs/adr/`, written when the
decision is made rather than reconstructed later. Copy `0000-template.md`.

## Commits

Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`.
Scope by phase where useful — `feat(phase2): verify cognito jwt`.
