# ADR-0002: pgvector first, S3 Vectors second

- **Status:** accepted
- **Date:** 2026-08-30
- **Phase:** 4

## Context

CloudGuard AI requires vector similarity search over document chunks to ground LLM reasoning in enterprise compliance documents.

The vector persistence solution must:
1. Preserve strict multi-tenant isolation and row-level security (RLS).
2. Filter chunks by tenant ID and caller confidentiality clearance (internal, confidential, restricted).
3. Work seamlessly across both local development (PostgreSQL + SQLAlchemy) and deployed serverless AWS (Aurora Serverless v2 via Data API).
4. Keep operational cost and architectural complexity minimal during earlier development phases.

## Options

| Option | Pros | Cons |
|---|---|---|
| Collocated pgvector (PostgreSQL / Aurora Serverless v2) | Single system of record; transactional consistency with chunk tables; native RLS tenant isolation; zero additional AWS infrastructure or cluster costs | Shares buffer pool and compute with relational database |
| Dedicated Vector Database (OpenSearch Serverless, Pinecone, Qdrant) | Specialized scaling for huge vector indices | Substantial additional idle cost ($20–$100+/mo); synchronisation lag; dual-write failure modes; fragmented authorization model |
| S3 Vectors / Custom Object Store | Extremely cheap cold storage for vector indices | High query latency; complex custom indexing and partitioning logic needed |

## Decision

Adopt `pgvector` with 1024-dimensional embeddings (Amazon Titan Text Embeddings v2) and HNSW cosine distance indexing (`vector_cosine_ops`) collocated on the primary PostgreSQL / Aurora database.

Vector operations are abstracted behind the `VectorStore` domain port (`backend/app/ports/vector_store.py`), with two concrete implementations:
- Local development & CI: `SQLAlchemyVectorStore` using `pgvector.sqlalchemy` and `tenant_session()`.
- Deployed AWS Lambda: `AuroraDataAPIVectorStore` executing parameterized SQL with `:embedding::vector` type casts over the Aurora Data API.

The architecture defers S3 Vectors to Phase 11 as a secondary adapter to benchmark against pgvector.

## Consequences

- **What this buys:** Collocated metadata and vector queries, transactional consistency when updating chunks and embeddings, zero added infrastructure spend, and robust tenant isolation enforced by PostgreSQL RLS.
- **What this costs:** Memory on the Aurora cluster is shared between relational queries and HNSW index graphs.
- **What it makes harder:** Scaling beyond millions of vectors per database instance would require scaling Aurora compute or moving to S3-backed vector storage.

## Revisit when

The vector corpus exceeds 10 million vectors, or the Phase 11 benchmark demonstrates that S3-backed vector retrieval provides materially superior cost-efficiency for archive workloads.
