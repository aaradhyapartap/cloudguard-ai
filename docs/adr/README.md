# Architecture Decision Records

One file per decision, written when the decision is made. The point is not
documentation for its own sake — it is that "why did you choose X?" in an
interview has a written answer that includes what you gave up, which is the
half most people cannot produce.

## Index

| ADR | Decision | Status | Phase |
|---|---|---|---|
| [0001](0001-rag-over-fine-tuning.md) | RAG over fine-tuning | accepted | 0 |
| [0002](0002-pgvector-first-s3-vectors-second.md) | pgvector first, S3 Vectors second | accepted | 4 |
| 0003 | Postgres as system of record, DynamoDB for audit | accepted | 0 |
| 0004 | Custom orchestration over Bedrock AgentCore | accepted | 0 |
| 0005 | Deterministic risk scoring in Python | accepted | 0 |
| 0006 | Fixed agent graph, no emergent delegation | accepted | 0 |
| 0007 | Hybrid retrieval from the MVP | accepted | 0 |
| [0008](0008-lambda-outside-vpc-via-aurora-data-api.md) | Lambda outside VPC via Aurora Data API | accepted | 3 |
| 0009 | CDK (Python) over Terraform | accepted | 0 |
| [0010](0010-citation-verification-in-application-code.md) | Citation verification in application code | accepted | 4 |
| 0011 | Cassette-based LLM provider for tests | accepted | 0 |
| 0012 | Static SPA over Next.js SSR | accepted | 0 |
| [0013](0013-ports-and-adapters.md) | Ports and adapters as the module boundary | accepted | 1 |
| [0014](0014-local-auth-mirrors-production.md) | Local development uses real signed tokens | accepted | 2 |
| [0015](0015-token-storage-and-id-tokens.md) | ID tokens as API credentials, sessionStorage in the SPA | accepted | 2 |
| [0016](0016-no-security-definer-view.md) | Local login reads a code fixture, not the database | accepted | 2 |

ADRs 0002–0012 are decided and summarised in
[`../architecture/00-phase0-architecture.md`](../architecture/00-phase0-architecture.md) §P.
Each gets its own file when the phase that implements it lands, so the
consequences section can record what actually happened rather than what was
predicted.
