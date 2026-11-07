# Architecture Decision Records

One file per decision, written when the decision is made. The point is not
documentation for its own sake — it is that "why did you choose X?" in an
interview has a written answer that includes what you gave up, which is the
half most people cannot produce.

## Index

| ADR | Decision | Status | Phase |
|---|---|---|---|
| [0001](0001-rag-over-fine-tuning.md) | RAG over fine-tuning | accepted | 0 |
| [0002](0002-pgvector-first-s3-vectors-second.md) | pgvector first, S3 Vectors second | accepted (both adapters implemented) | 4, 11 |
| 0003 | Postgres as system of record, DynamoDB for audit | accepted (superseded for approval audit by [0017](0017-postgresql-approval-audit-canonical.md); DynamoDB used for admission per [0018](0018-shared-api-rate-admission.md)) | 0, 8 |
| 0004 | Custom orchestration over Bedrock AgentCore | accepted | 0 |
| [0005](0005-deterministic-risk-scoring-in-python.md) | Deterministic risk scoring in Python | accepted | 5 |
| 0006 | Fixed agent graph, no emergent delegation | accepted | 0 |
| 0007 | Hybrid retrieval from the MVP | accepted | 0 |
| [0008](0008-lambda-outside-vpc-via-aurora-data-api.md) | Lambda outside VPC via Aurora Data API | accepted (implemented for workers; HTTP API remains TCP `asyncpg`) | 3, 7, 11 |
| 0009 | CDK (Python) over Terraform | accepted | 0 |
| [0010](0010-citation-verification-in-application-code.md) | Citation verification in application code | accepted | 4 |
| 0011 | Cassette-based / deterministic fixture LLM provider for tests | accepted | 0, 9 |
| 0012 | Static SPA over Next.js SSR | accepted | 0 |
| [0013](0013-ports-and-adapters.md) | Ports and adapters as the module boundary | accepted (17 port modules: 13 with AWS adapters, 4 local/SQLAlchemy-only on deferred HTTP API boundary) | 1–11 |
| [0014](0014-local-auth-mirrors-production.md) | Local development uses real signed tokens | accepted | 2 |
| [0015](0015-token-storage-and-id-tokens.md) | ID tokens as API credentials, sessionStorage in the SPA | accepted, with known compromises (PKCE flow implemented in Phase 8.5) | 2, 8 |
| [0016](0016-no-security-definer-view.md) | Local login reads a code fixture, not the database | accepted | 2 |
| [0017](0017-postgresql-approval-audit-canonical.md) | PostgreSQL is canonical for approval audit events | accepted | 8 |
| [0018](0018-shared-api-rate-admission.md) | Shared single-region API admission counters | accepted (implemented in source & CDK; live deployment verification pending) | 8 |

ADRs 0003, 0004, 0006, 0007, 0009, 0011, and 0012 are summarised in
[`../architecture/00-phase0-architecture.md`](../architecture/00-phase0-architecture.md) §P.
Dedicated files exist for every decision whose implementation introduced
specific engineering trade-offs, database invariants, or security boundaries.
