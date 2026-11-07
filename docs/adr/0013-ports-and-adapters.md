# ADR-0013: Ports and adapters as the module boundary

- **Status:** accepted
- **Date:** 2026-08-22
- **Phase:** 1

## Context

Every external dependency in this system is either expensive (Bedrock),
non-deterministic (Bedrock again), or unavailable offline (all of them). Writing
`boto3` calls inline across services would make the application untestable
without an AWS account and unrefactorable without spending money.

Phase 0 §K committed to a local development strategy where the whole application
runs with no AWS account. That commitment has to be implemented by something.

## Options

| Option | Pros | Cons |
|---|---|---|
| Direct SDK calls in services | Least code; obvious | Untestable offline; every test costs money and returns different output |
| `moto`/LocalStack for everything | Realistic AWS behaviour | No Bedrock emulation exists — the one dependency that matters most is not covered |
| Ports with swappable adapters | Free, deterministic tests; provider substitution possible | An indirection layer to maintain; a translation cost per adapter |

## Decision

Four ports — `LLMProvider`, `VectorStore`, `DocumentStore`, `EventPublisher` —
in `app/ports/`, with implementations in `app/adapters/`. One composition root
(`app/core/container.py`) binds them from configuration. Selection is an
environment variable.

Enforced mechanically: Ruff's `banned-api` rule fails the build on any `boto3`
import outside `app/adapters/`. An architectural rule that is only in a document
is a rule that erodes.

## Consequences

The full application runs locally against test doubles at zero cost. Tests are
deterministic, so they stay enabled.

The cost is real and worth naming: every adapter needs a translation layer
between the domain type and the vendor payload, and an abstraction with one
implementation is speculative design. That is why ADR-0002 commits to building
a *second* real `VectorStore` adapter in Phase 11 — two adapters prove the port,
one proves nothing.

Adapters that do not exist yet raise `AdapterNotAvailableError` at startup,
naming the phase that adds them. Deferral is explicit and fails loudly rather
than silently returning `None`.

## Revisit when

If by Phase 11 there is still exactly one implementation of every port, the
abstraction did not pay for itself and should be collapsed.

**Phase 12 outcome:** By Phase 11, the `app/ports/` boundary grew to 17 domain
port modules (18 abstract interfaces). **13 port modules** have both local/test
adapters and AWS/cloud adapters: `document_store` (`S3DocumentStore`),
`event_publisher` (`EventBridgePublisher`), `identity_provider`
(`CognitoIdentityProvider`), `llm_provider` (`BedrockLLMProvider` +
`BedrockEmbeddingProvider`), `vector_store` (`AuroraDataAPIVectorStore` +
`S3VectorStore`), `rate_limit_store` (`DynamoDBRateLimitStore`),
`work_lease_store` (`DynamoDBWorkLeaseStore`), `telemetry`
(`CloudWatchEMFTelemetryEmitter`), `document_processing_repository`
(`AuroraDataAPIDocumentProcessingRepository`), `approval_creation_repository`
(`AuroraDataAPIApprovalCreationRepository`), `approval_callback`
(`StepFunctionsApprovalCallbackAdapter`), `approval_callback_recovery_repository`
(`AuroraDataAPIApprovalCallbackRecoveryRepository`), and
`approval_execution_repository` (`AuroraDataAPIApprovalExecutionRepository`).
The remaining **4 port modules** (`approval_repository`, `compliance_repository`,
`evaluation_repository`, and `approved_action_executor`) currently have
local/SQLAlchemy implementations only: in deployed AWS workflows, bounded
approved-action execution (`create_security_review`) is performed directly inside
`AuroraDataAPIApprovalExecutionRepository.execute_for_workflow`, while
`ApprovalRepository`, `ComplianceRepository`, and `EvaluationRepository` back
the synchronous FastAPI HTTP routes and local workflows on the deferred HTTP API
Data API persistence boundary ([ADR-0008](0008-lambda-outside-vpc-via-aurora-data-api.md)).
Throughout, `app/services/` remains completely free of `boto3` and SQLAlchemy types.
