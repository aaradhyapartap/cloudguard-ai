# CloudGuard AI

**Agentic audit & compliance intelligence platform on AWS**

Upload a governance corpus — policies, controls documentation, audit reports,
vendor assessments — and get evidence-linked answers, detected control gaps,
deterministically scored risk, and an auditable trail from every recommendation
back to the chunks that produced it and the human who approved it.

> **Status: Phase 2 of 12 — Authentication.**
> The skeleton runs end to end and is authenticated. Document ingestion
> (Phase 3) and retrieval (Phase 4) are not built yet. The
> [architecture](docs/architecture/00-phase0-architecture.md) is complete and
> the roadmap is in §N of that document.

---

## What Phases 1–2 deliver

| | |
|---|---|
| Backend | FastAPI on Python 3.12, layered `api → services → repositories`, Lambda-ready via Mangum |
| Configuration | Typed and validated at startup — a bad env var kills the process with a message naming the field |
| Logging | Structured JSON, request-correlated, with automatic redaction of sensitive keys |
| Ports & adapters | 4 ports with in-memory adapters, so the whole app runs with no AWS account |
| Persistence | SQLAlchemy 2.0 async, Alembic migrations, **PostgreSQL Row-Level Security** |
| Authorization | Server-side permission matrix, 3 roles, exhaustively tested |
| Authentication | JWT bearer tokens — Cognito RS256 in AWS, local HS256 offline, one verification path |
| Identity | Just-in-time user provisioning; the token is authoritative for role |
| Infrastructure | CDK identity stack: user pool, role groups, custom claims, hosted UI |
| Frontend | Next.js 15 static export, TypeScript, Tailwind v4, enterprise console shell |
| CI | Ruff, mypy `--strict`, pytest with real PostgreSQL, secret and dependency scanning |

**149 tests passing** (including 13 token-forgery cases and 7 Row-Level Security
tests against real PostgreSQL). **`ruff` clean. `mypy --strict` clean.**

## Quick start

```bash
git clone <your-repo-url> cloudguard-ai
cd cloudguard-ai
./scripts/local_setup.sh
```

Then in two terminals:

```bash
make api    # http://localhost:8000/docs
make web    # http://localhost:3000
```

The dashboard should show a green **API connected** badge and the analyst
principal the server resolved.

<details>
<summary>Manual setup, if you prefer</summary>

```bash
cp .env.example .env
cp frontend/.env.local.example frontend/.env.local

cd backend && pip install -e ".[dev]" && cd ..
cd frontend && npm install && cd ..

docker compose up -d
cd backend && alembic upgrade head && cd ..
python scripts/seed_data.py
```
</details>

> **Build-time note.** `next/font/google` downloads IBM Plex at build time and
> self-hosts it in the output — there is no runtime request to Google. That does
> mean `npm run build` needs network access to `fonts.googleapis.com`. In a
> locked-down CI network, switch `src/app/layout.tsx` to `next/font/local` and
> vendor the font files.

## Verify it works

```bash
make check                    # lint + typecheck + tests (no database needed)
make test-db                  # tenant isolation tests against real PostgreSQL
```

Log in and prove the authorization boundary by hand:

```bash
TOKEN=$(make token)                                     # analyst@acme.test

curl -s localhost:8000/api/v1/me -H "Authorization: Bearer $TOKEN" | jq
curl -s -o /dev/null -w '%{http_code}\n' \
     localhost:8000/api/v1/system/config -H "Authorization: Bearer $TOKEN"   # 403
```

An analyst gets `403` on the admin endpoint. Log in as `admin@acme.test` and it
returns `200`. The frontend never participates in that decision.

Tamper with the token — change a character in the payload — and every request
becomes `401` with a message that does not say which check failed.

## Architecture

Full design: [`docs/architecture/00-phase0-architecture.md`](docs/architecture/00-phase0-architecture.md).
Decisions and their trade-offs: [`docs/adr/`](docs/adr/).

```
Browser ─ CloudFront ─ S3 (static Next.js)
   │
   └─ API Gateway (JWT authorizer) ─ Lambda / FastAPI
                                        ├─ Aurora Serverless v2 + pgvector   (system of record, RLS)
                                        ├─ DynamoDB                          (audit, agent traces)
                                        ├─ S3                                (documents)
                                        ├─ EventBridge ─ Step Functions      (ingestion, agents, approval)
                                        └─ Bedrock + Guardrails              (generation, embeddings)
```

Three properties are load-bearing and worth reading the code for:

**Tenant isolation is enforced twice.** Repositories scope every query by
`organization_id`, *and* PostgreSQL RLS policies refuse to return other tenants'
rows regardless of what the query says. `tests/integration/test_tenant_isolation.py`
runs a deliberately buggy `SELECT * FROM documents` with no filter and asserts
the database still returns only one tenant's rows.

**The application does not know what a Bedrock is.** Services depend on ports in
`app/ports/`. Ruff fails the build if `boto3` is imported outside
`app/adapters/`. This is why the whole system runs locally for free.

**Authorization is data, tested exhaustively.** One matrix in
`app/security/authz.py`; `tests/unit/test_authz.py` asserts every
(role, permission) pair rather than the handful someone remembered.

## Project layout

```
backend/app/
  api/            routers — thin, no business logic
  core/           config, logging, errors, container
  models/         Pydantic domain models, Principal
  ports/          ← the abstraction boundary
  adapters/       ← the only place a cloud SDK may appear
  repositories/   persistence, always Principal-scoped
  security/       authorization matrix, claims mapping
  services/       business logic — no cloud SDKs
infrastructure/   AWS CDK (Python)
frontend/src/     Next.js app shell
docs/adr/         architecture decision records
scripts/          local bootstrap and seeding
```

## Roadmap

| Phase | Scope | State |
|---|---|---|
| 0 | Architecture | ✅ |
| 1 | Foundation | ✅ |
| 2 | Cognito authentication, RBAC end to end | ✅ |
| 3 | Document ingestion pipeline | next |
| 4 | RAG: chunking, embeddings, retrieval, citations | |
| 5 | Risk engine and dashboard | ← demoable from here |
| 6–7 | Agents; human approval | |
| 8–9 | Guardrails and security; AI evaluation | |
| 10–12 | Observability; IaC and CI/CD; portfolio polish | |

## Security

Never commit `.env`. Deployed environments inject configuration from Secrets
Manager or SSM; the repository ships only `.env.example`.

Authentication is JWT bearer tokens in every environment. The local signer is
refused at startup unless `ENVIRONMENT=local`, and `/auth/dev-login` is absent
from the routing table elsewhere rather than guarded inside the handler — a
route that does not exist cannot be misconfigured back into existence.

Known limitation, documented rather than buried: the static SPA stores its token
in `sessionStorage`, so an XSS bug in this application is a session-theft bug.
See [ADR-0015](docs/adr/0015-token-storage-and-id-tokens.md).

`SECURITY.md` and `docs/THREAT_MODEL.md` arrive in Phase 8.

## Licence

MIT — see [LICENSE](LICENSE).
