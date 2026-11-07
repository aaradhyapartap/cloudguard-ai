# Response projections and runtime privacy

## Evidence and metadata

Retrieval still admits documents using the original authenticated Principal, tenant
predicates, and server-derived confidentiality levels. RLS and deterministic source
membership checks are unchanged. Authorized original evidence text and quotes are
not rewritten or silently discarded. Sensitivity detection is not declassification.

Retrieval results and RAG sources now expose only allowlisted metadata: bounded
integer page/chunk indexes, recognized content types/confidentiality labels, and
recognized model-data policy/category annotations. Filenames, storage keys, task
tokens, arbitrary nested objects, and unknown metadata fields are excluded from
these projections. Original stored metadata is unchanged. Metadata confidentiality
labels are informational; database document confidentiality remains authoritative.

Document registration's explicitly issued upload URL is an intentional bounded
capability, not a metadata field to strip. It remains subject to authentication,
tenant-prefixed key issuance, and expiry. Authorized document filename endpoints
remain explicit document contracts; the source-metadata allowlist is not a claim
that no authorized endpoint can return a filename or original evidence.

## API error boundary

Intentional application errors retain their HTTP status and stable error code.
Their arbitrary internal `details` dictionaries are no longer returned through the
generic handler. Detected sensitive patterns in custom messages cause a class-default
message to be returned. Pattern detection has the limitations in DATA_HANDLING.md;
it cannot recognize every opaque secret. Current dynamic error producers were
reviewed: mock storage no longer includes keys, retrieval bounds no longer forward
raw validator text, and extraction errors use application-authored messages.

Request validation continues to use the generic non-reflecting projection from
slice 1. Authentication logs a fixed rejection category instead of provider text.
Neither middleware nor authenticated dependencies bind raw request paths to logs.

## Configured Python logging

Application structured events traverse the redaction processor before a private
marker allows their rendered text through the standard-library handler. Unmarked
foreign log records are replaced with a fixed `external_log` event and severity
before message interpolation; their message, arguments, stack, and exception text
are not emitted. Known Uvicorn, SQLAlchemy, AWS SDK, HTTP client, and Mangum logger
handlers are routed to this configured handler. Safe application request events
provide status and timing without restoring foreign access-log payloads.

The marker distinguishes trusted application plumbing, not hostile Python code.
This policy sacrifices foreign message detail for privacy. A third-party library
that installs a separate handler later, direct stdout/stderr writes, import/cold-start
errors before configuration, provider/platform logs, and externally configured
handlers are outside this handler's guarantee. Deployment must review those paths.

## Worker and Step Functions boundary

All eight current worker entrypoints wrap task failures in `WorkerTaskError` with
a fixed message and suppressed exception context. Success values are unchanged.
No event values or raw exception strings are logged by the wrapper. Python task
failure serialization no longer includes Pydantic input values or arbitrary provider
messages. This deliberately changes failure exception names to a stable safe type;
the current state machines catch `States.ALL`. Future typed retries/catches must
explicitly account for this contract. The wrapper does not change callback delivery,
persisted decisions, task-token storage, execution policy, or idempotency.

Both state-machine templates explicitly set CloudWatch execution logging to OFF
and IncludeExecutionData to false. This prevents an additional CloudWatch execution
payload copy when the template is deployed. It does not remove native Standard
Workflow history, task inputs/outputs, or service-required capabilities. The existing
workflow intentionally carries questions, Principal claims, evidence, and an internal
approval task token; deleting these without redesign would break the workflow.

Template regressions also reject inline workload grants that match
`states:GetExecutionHistory` or `states:DescribeExecution`. Operator/console access,
organization policies, existing deployed resources, externally attached policies,
DLQ access/retention, and execution-history retention are separate deployment
responsibilities. No deployment or live AWS policy verification occurred here.
Never claim that CloudWatch payload exclusion disables native execution history.

References:

- https://docs.aws.amazon.com/step-functions/latest/dg/cw-logs.html
- https://docs.aws.amazon.com/step-functions/latest/apireference/API_GetExecutionHistory.html
- https://structlog.org/en/stable/processors.html

## Validation and residuals

Required local gates include backend focused/full tests, Ruff/mypy, infrastructure
template synthesis/tests, and immutable-migration/diff/inventory checks. Native
history and authorized original evidence are documented protected surfaces, not
claims of zero sensitive data at rest. Detector recall, Python 3.12 CI, live-provider
evaluation, reindexing, runtime configuration, and deployment IAM/log reviews remain
separate verification. Phase 8.4 resource/abuse bounds and Phase 8.5 blocking scans
are still outstanding; do not mark Phase 8 complete.
