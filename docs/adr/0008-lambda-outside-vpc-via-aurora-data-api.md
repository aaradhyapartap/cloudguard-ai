# ADR-0008: Lambda outside VPC via Aurora Data API

- **Status:** accepted
- **Date:** 2026-08-29
- **Phase:** 3

## Context

CloudGuard AI uses Aurora Serverless v2 PostgreSQL as its system of record and
pgvector store. The application is deployed primarily on AWS Lambda.

A direct PostgreSQL connection from Lambda normally requires placing the
function inside the database VPC. Once Lambda is inside private subnets, reaching
public AWS services can require NAT or interface endpoints. For the portfolio
and demo environment, a NAT Gateway creates a large fixed monthly cost relative
to the rest of the stack.

The architecture therefore needs a database access pattern that preserves the
serverless cost model, avoids a NAT Gateway, and still keeps Aurora as the
system of record.

The local and CI environments continue to use PostgreSQL directly through
SQLAlchemy and asyncpg. This ADR governs the deployed AWS path.

## Options

| Option | Pros | Cons |
|---|---|---|
| Lambda in private subnets with direct PostgreSQL connections | Native PostgreSQL protocol; existing SQLAlchemy repositories work unchanged | Requires VPC networking; adds connection-management concerns; private-subnet access to other AWS services may require NAT or interface endpoints |
| Lambda outside the VPC using Aurora Data API | HTTPS-based access; no Lambda VPC attachment; no NAT Gateway required; no persistent connection pool to manage | Requires a Data API persistence adapter; not every PostgreSQL feature maps cleanly; pgvector parameter/result handling must be validated |
| Lambda in VPC with RDS Proxy | Better connection management for bursty Lambda workloads; native PostgreSQL semantics | Still requires VPC networking and additional infrastructure/cost; does not solve outbound-access cost by itself |

## Decision

Deployed Lambda functions remain outside the VPC and access Aurora Serverless v2
through the Aurora Data API.

The application keeps its existing repository and service boundaries. AWS
database access must be implemented behind a persistence adapter rather than by
importing AWS SDK calls into services.

Local development and CI continue to use direct PostgreSQL connections through
SQLAlchemy and asyncpg because they provide fast, realistic RLS integration
tests without requiring AWS.

Phase 3 ingestion orchestration must not deploy the current asyncpg-backed
`tenant_session()` inside an out-of-VPC Lambda. The deployed ingestion path will
use Step Functions and a Data API-backed persistence boundary when database
access is required.

## Consequences

This keeps Lambda outside the VPC, avoids a NAT Gateway, and preserves the
low-idle-cost design of the project.

The cost is maintaining two infrastructure-facing persistence implementations:
direct PostgreSQL for local/CI and Aurora Data API for deployed AWS workloads.

The Data API adapter must preserve tenant isolation explicitly. Any operation on
an RLS-protected table must establish the tenant context before executing
tenant-scoped SQL.

pgvector compatibility through the Data API remains a validation item for the
RAG phase. Vector parameters may require explicit PostgreSQL casts, and code
should avoid returning raw vector values when IDs and scores are sufficient.

The application service layer remains cloud-SDK-free. AWS-specific Data API
calls belong behind adapters, consistent with ADR-0013.

## Revisit when

Revisit this decision if any of the following becomes true:

- Data API limitations materially block pgvector or transaction semantics.
- Database traffic or latency makes the HTTPS Data API path a measurable
  bottleneck.
- The application must run inside private subnets for compliance reasons.
- Interface endpoints plus RDS Proxy become operationally or financially
  preferable to the Data API.
