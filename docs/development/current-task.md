# Current Development Task

## Phase

Phase 3 - Document Ingestion

## Branch

phase-3-document-ingestion

## Current status

The following Phase 3 components are already implemented and pushed:

- document registration
- presigned upload flow
- upload completion flow
- text extraction and deterministic chunking
- document chunk persistence
- tenant RLS tests
- event-driven local processing worker
- DocumentProcessingRepository port
- local SQLAlchemy processing adapter
- Aurora Data API processing adapter
- deployed Data API document worker
- normalized Step Functions task contract
- Phase 3 validation automation

## Current next task

Implement the production ingestion orchestration/CDK slice:

S3 ObjectCreated
-> EventBridge
-> Step Functions Standard workflow
-> deployed document-processing Lambda

The deployed processing entrypoint is:

app.deployed_document_worker.handler

The deployed worker expects:

{
    "organization_id": "<uuid>",
    "document_id": "<uuid>"
}

## Required architecture

- S3 ObjectCreated triggers EventBridge.
- EventBridge starts Step Functions.
- Step Functions normalizes upstream event data.
- Step Functions invokes app.deployed_document_worker.handler.
- Deployed Lambda uses Aurora Data API.
- Deployed Lambda does not use tenant_session().
- Lambda remains outside the database VPC.
- No NAT Gateway.
- SQS is used for ingestion failure/DLQ handling.
- Preserve PostgreSQL RLS.
- Do not bypass Step Functions.

## Before editing

Read:

- AGENTS.md
- docs/development/phase3-agent-playbook.md
- docs/architecture/00-phase0-architecture.md
- docs/adr/0008-lambda-outside-vpc-via-aurora-data-api.md
- infrastructure/app.py
- infrastructure/stacks/
- backend/app/deployed_document_worker.py

## Acceptance criteria for the next slice

- Phase 3 infrastructure stack exists.
- S3 document bucket exists.
- S3 ObjectCreated events reach EventBridge.
- EventBridge starts a Step Functions Standard workflow.
- Worker input is normalized to organization_id + document_id.
- Processing task uses app.deployed_document_worker.handler.
- Processing Lambda is outside the VPC.
- No NAT Gateway is created.
- SQS DLQ/failure path exists.
- IAM permissions are scoped.
- Infrastructure tests or synth validation pass.
- Backend validation remains green.
- git diff --check passes.

## Stop conditions

Stop and request review if:

- organization_id/document_id cannot be derived safely from the S3 event/key
- Step Functions JSONPath/string handling becomes fragile
- a normalization Lambda appears necessary
- Lambda would need VPC access
- NAT Gateway appears necessary
- RLS or Data API semantics would need weakening
- the implementation would bypass EventBridge or Step Functions

## Validation

Backend Phase 3 validation:

powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase3.ps1

Full project validation:

powershell -ExecutionPolicy Bypass -File .\scripts\validate-all.ps1

## Commit policy

Do not commit or push until:

- focused validation passes
- full validation passes
- git diff --check passes
- architecture review is complete
