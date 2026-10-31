# Safe application logging and error responses

Phase 8.3 slice 1 removes raw exception strings, rollback transaction IDs, and S3
object keys from application logging call sites. Request middleware no longer logs
raw URL paths, which can contain attacker-selected identifiers or sensitive text.
Application errors log their stable error code and HTTP status, not their message.

Use static event names, bounded event categories, counts, timings, and safe internal
identifiers. Never log document text, model prompts, filenames, object keys,
credentials, bearer tokens, task tokens, presigned URLs, or raw exceptions.

## Redaction boundary

The final structured-log processor traverses mappings, lists, and tuples without
mutating the original values. Sensitive key fragments and designated free-text
error/body keys are redacted. Binary data, exceptions, and opaque objects are not
rendered. A depth ceiling terminates cyclic or excessively nested structures.
Exception and stack-rendering inputs are removed before either JSON or console
rendering, so a chained exception cannot restore sensitive text after redaction.

This is key-based defense in depth, not universal content detection. A sensitive
string placed under an innocuous key can still leak. The static logging regression
checks current application call sites; it does not establish a third-party logging
guarantee. Standard-library logs, Uvicorn/platform exception logging, and deployed
workflow execution-history controls still need review before Phase 8.3 is complete.

## Validation errors

FastAPI request-validation failures retain HTTP 422 and `VALIDATION_FAILED`. Their
details contain at most 50 generic errors with only the request location category
(`body`, `query`, `path`, `header`, or `cookie`). Submitted values, arbitrary field
names, validator text, error contexts, and documentation URLs are not reflected.
Clients must not depend on the former detailed Pydantic error representation.

Existing intentional application-error response messages and details are unchanged
by this slice and require a separate sensitive-data review.

## Remaining Phase 8.3 work

- Provider-neutral PII and credential detection/redaction.
- Minimized model input and checked model output across all generation paths.
- Auditable redaction metadata while preserving original compliance evidence.
- Safe public metadata/evidence projections and confidentiality regression review.
- Standard-library/platform log and intentional application-error response review.

This slice does not change document storage, retrieval authorization, scoring,
approval authority, database privileges, or published migrations.
