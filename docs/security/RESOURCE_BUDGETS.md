# Model and workflow resource budgets

Phase 8.4c adds application-owned resource ceilings without replacing models,
weakening authorization, truncating original evidence, or shortening the human
approval wait. These limits are not a complete spend cap or parser sandbox.

## Model admission and response limits

| Boundary | Ceiling |
| --- | --- |
| Complete serialized GenerationRequest envelope | 128,000 UTF-8 bytes |
| Generation messages | 1-32 |
| Requested output tokens | 1-4,096 |
| Bedrock output tokens actually requested | Minimum of request and configured ceiling (default 2,048) |
| Stop sequences | At most 8, each at most 128 characters |
| Consumed generation response text | 64,000 UTF-8 bytes |
| Embedding texts per batch | 200 |
| UTF-8 bytes per embedding text | 8,192 |
| Aggregate UTF-8 bytes per embedding batch | 800,000 |
| Serialized embedding response | 128,000 bytes per model call |

`app/security/model_budgets.py` owns the model ceilings. All seven generation paths
construct GenerationRequest, whose validation includes messages, system prompt,
response schema, stop sequences, and other request fields in the envelope budget.
The budget counts the application's compact JSON serialization, not an exact
Bedrock HTTP request size. It is deliberately measured in bytes, not estimated
tokens. A preliminary text-length check avoids serializing obviously oversized
message collections. Application-owned response schemas still need bounded
construction; this validator is not a process memory sandbox.

Bedrock revalidates a copied request immediately before dispatch, including nested
mutable lists and model_copy updates. BEDROCK_MAX_INPUT_BYTES can lower the byte
ceiling but cannot raise the hard maximum. BEDROCK_MAX_OUTPUT_TOKENS is now a real
ceiling: a larger GenerationRequest.max_tokens cannot override it. Invalid or
over-budget dispatches fail closed with a static resource-budget error before any
provider call. Initial GenerationRequest construction can raise a validation error
before provider normalization; existing safe API/worker error boundaries apply.

Generation output size is checked before shared JSON/sensitive-data scans. Bedrock
also checks text blocks while assembling its response. These are post-provider
checks and cannot refund an already-billed invocation or prevent the SDK from
initially receiving a response. All seven service paths retain their shared
response-safety checks.

Both local/mock and Bedrock embedding providers validate the entire batch before
the first embedding operation. A later oversized text cannot cause a partially
dispatched batch. Bedrock response streams use bounded reads and are closed on
success or failure; blocking stream I/O runs off the event loop. Original stored
evidence is not truncated or silently replaced when a budget is exceeded.

## Native-token compatibility constraint

BEDROCK_MAX_INPUT_TOKENS remains a reserved target, not an enforced tokenizer
count. The configured Nova Pro reviewer does not support Bedrock CountTokens in
the model's documented feature matrix. This patch does not silently change the
reviewer, add a cross-provider credential, or claim that bytes/4 is a token count.
Native-token enforcement needs an explicitly supported model/tokenizer choice and
separate provider/region/IAM verification. Mock usage remains a test estimate, not
billing evidence. Output-token ceilings are sent to the existing Bedrock API.

References:

- [Nova Pro feature matrix](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-pro.html)
- [Bedrock token counting](https://docs.aws.amazon.com/bedrock/latest/userguide/count-tokens.html)

## Transport and workflow attempts

Bedrock generation and embedding clients use total_max_attempts=1: one SDK attempt,
not one retry plus an initial call. Socket connection timeouts are 3 seconds;
generation read timeout is 60 seconds and embedding read timeout is 30 seconds.
Socket timeouts do not create a full wall-clock deadline or cancel an invocation
already accepted by Bedrock. No asyncio timeout is described as killing a thread
or reversing an inference bill. Caller cancellation/provider transport failures
can still leave an uncertain provider outcome.

Every Step Functions Lambda task disables CDK's implicit Lambda service retries.
Only Lambda.TooManyRequestsException receives two retries (three admission attempts
total), with a 2-second interval and exponential factor 2. Application exceptions,
model failures, function timeouts, and uncertain service/SDK outcomes are not
automatically replayed by these task retriers. Existing catches remain intact.
This deliberately favors bounded work over retrying ambiguous paid/side-effect
outcomes; it is not a distributed exactly-once guarantee.

The S3/EventBridge ingestion target has two delivery retries, a one-hour event age,
and its existing DLQ. Delivery retries can still duplicate workflow starts; they
are distinct from task retries. Redrive, replay, and separately submitted events
can consume a new budget and remain operationally controlled actions.

## Concurrency and retained approval behavior

Research, Risk, Reviewer, and document-processing Lambdas each reserve concurrency
2. These are four separate regional function ceilings, not a tenant semaphore or
a total admitted-execution cap. The existing 120-second model-worker and
300-second ingestion-worker timeouts are retained. Verify account reserved and
unreserved concurrency quotas before deployment; do not remove limits just to
force a deployment through. API model calls are not covered by these four worker
reservations, because the repository does not yet define the API Lambda stack.
The short admission retry window can exhaust while another worker is still busy:
ingestion then follows its DLQ path rather than waiting indefinitely. This is a
bounded-availability tradeoff, not tenant fairness or automatic safe replay.

The Standard agent workflow still allows the existing 365-day human approval
wait. Approval task tokens, Manager-only decisions, callback recovery, and the
persisted approved-action boundary are unchanged. Pending human approvals do not
hold a running model Lambda. The ingestion failure catch and DLQ path remain.

## Remaining requirements

- Full PostgreSQL-backed regressions, backend Ruff/mypy, CDK assertions, and Python
  3.12 CI; local checks are not evidence of live AWS enforcement.
- Explicit native input-token compatibility and enforcement decision.
- Distributed workflow/ingestion-start admission and tenant concurrency leases.
- API Lambda admission/environment wiring, API reserved concurrency, and edge/WAF
  limits for volumetric attacks, queued workflows, and counter-store costs.
- Parser CPU/decompression isolation, storage-side upload limits, and a total
  execution/lifetime spend budget that accounts for replay and callback recovery.
- Live provider, retry, account-quota, and deployment drift verification.

No model replacement, AWS deployment, external IAM mutation, migration, backfill,
or data deletion is performed by this patch. Phase 8.4 and Phase 8 remain incomplete.
