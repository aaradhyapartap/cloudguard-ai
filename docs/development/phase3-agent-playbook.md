# Phase 3 Agent Playbook — Document Ingestion

This playbook guides coding agents through the remaining CloudGuard AI Phase 3 document-ingestion work.

## 1. Current objective

- Finish Phase 3 document ingestion.
- The current branch and repository contents are the source of truth.
- Do not begin Phase 4 until Phase 3 validation is green.

## 2. Already implemented

The following Phase 3 capabilities are complete and passing validation:

- Document registration
- Presigned upload flow
- Upload completion flow
- Text extraction and deterministic chunking
- Document chunk persistence
- Tenant RLS tests
- Event-driven processing service
- `DocumentProcessingRepository` port
- Local SQLAlchemy processing adapter
- Aurora Data API adapter foundation
- Data API transaction/RLS helpers
- Data API `get_document` implementation and focused test
- `scripts/validate-phase3.ps1`

## 3. Current task

Continue `backend/app/adapters/aws/document_processing_repository.py`.

Complete and test `set_status` and `add_chunks`.

## 4. Data API transaction requirements

Every tenant-scoped operation must:

1. `begin_transaction`
2. Set `app.current_organization_id` using `set_config(..., true)` (the `true` makes it local to the transaction)
3. Execute tenant-filtered parameterized SQL
4. Commit on success
5. Rollback on failure

Do not bypass this sequence. The PostgreSQL RLS policies depend on `app.current_organization_id` being set before any tenant-scoped query executes.

## 5. Architecture constraints

- Lambda stays outside VPC.
- Aurora Data API for deployed persistence.
- No NAT Gateway.
- Preserve PostgreSQL RLS.
- `boto3` only in `app/adapters/`.
- Services depend on ports, not concrete adapters.
- Production ingestion must follow the accepted S3 → EventBridge → Step Functions architecture.
- Do not silently replace architecture. If architecture and implementation disagree, stop and surface the mismatch.

## 6. Validation

### Focused validation

```powershell
cd backend

python -m ruff check app/adapters/aws/document_processing_repository.py tests/unit/test_aws_document_processing_repository.py

python -m mypy app/adapters/aws/document_processing_repository.py tests/unit/test_aws_document_processing_repository.py

python -m pytest tests/unit/test_aws_document_processing_repository.py -q
```

### Full validation

Return to the repository root and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase3.ps1
```

The full validation script runs Ruff, mypy, pytest (with tenant RLS role), and `git diff --check`.

Do not commit if any validation step fails.

## 7. Remaining Phase 3 sequence

Complete in this order unless repository state shows a task is already done:

1. Finish Data API adapter (`set_status`, `add_chunks`, and any remaining methods)
2. Wire deployed persistence
3. Reconcile S3/EventBridge/Step Functions trigger
4. CDK infrastructure
5. PDF extraction
6. DLQ handling
7. Status/retry validation
8. Full validation
9. Commit/push

## 8. Stop conditions

Stop and ask the user rather than changing architecture if any of the following arise:

- NAT becomes necessary
- Lambda would need DB VPC attachment
- RLS cannot be preserved
- Step Functions would need to be bypassed
- The current completion-event flow conflicts with the S3 `ObjectCreated` design
- Data API transaction semantics are insufficient for the required operation

Do not silently work around these constraints.

## 9. Completion report

After each work slice, report:

- **Files changed** — list with brief description of each change
- **Behavior** — what the change accomplishes
- **Tests** — new or modified tests and what they cover
- **Ruff** — pass/fail
- **mypy** — pass/fail
- **pytest** — pass/fail with summary
- **Full validation** — pass/fail (when run)
- **git status** — `git status --short` output
- **Unresolved risks** — anything that may affect later work
- **Next recommended task** — what should be tackled next in the Phase 3 sequence
