# Per-document ingestion budgets

Phase 8.4a introduces centralized safety ceilings in
`app/security/ingestion_limits.py`:

| Boundary | Ceiling |
| --- | --- |
| Registered upload bytes | 10 MiB |
| Actual object bytes at completion and processing | 10 MiB |
| PDF pages | 200 |
| Extracted characters, including PDF page separators | 200,000 |
| Persisted chunks per processing attempt | 200 |

The existing 1,000-character chunk size is unchanged. Exact ceilings are accepted.
Zero-byte registrations remain compatible; empty extracted text still fails.
Missing, boolean, string, negative, or oversized actual-size metadata fails closed
at completion, before publishing the completion event.

The worker rechecks bytes from the object itself rather than trusting registration
or completion metadata. S3 processing reads at most the budget plus one sentinel
byte, supports short reads, and closes its response stream on success or failure.
The generic storage-port fallback checks after reading; any future streaming
adapter must override it to obtain the same memory bound as S3. The current mock
store already holds bytes in memory.

PDF page count is checked before extracting page text. Character count is checked
incrementally between pages and again before indexing. Chunk count is checked
before constructing the chunk list or running detection. Budget violations mark
processing FAILED, publish a static failure reason, and do not persist chunks,
invoke embedding, or upsert vectors. Original evidence is not truncated, deleted,
or silently rewritten. Existing encrypted-PDF quarantine behavior is retained.

## Remaining boundaries

These controls do not prevent an oversized object from being PUT to S3: the current
presigned PUT contract does not impose a storage-side content-length range.
Oversized objects remain in storage and need an explicit retention/lifecycle policy.
Upload overwrite races remain possible, but the worker checks the actual downloaded
bytes independently. Metadata validation is not proof of document integrity.

A small compressed PDF can still be expensive to parse or expand within a single
page. Page/text checks do not impose an in-parser CPU, memory, or decompression
budget; the deployed worker timeout is not a complete solution to this risk.
This slice does not add a parser sandbox or change workflow retries/timeouts.

Central route rate limiting, distributed tenant quotas, concurrent cost-bearing
work admission, model token ceilings, retry bounds, and edge/WAF deployment checks
remain Phase 8.4 work. Repeated workflow attempts can consume their per-document
budget again. No new infrastructure, migration, deployment, backfill, or deletion
is performed by this slice. Python 3.12 CI and full PostgreSQL-backed regression
validation are required before committing.
