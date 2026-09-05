# Current Task

Phase 7 - Human Approval and Consequential Action Gating

## Objective

Phase 7 extends a successfully reviewed agent workflow with a real human approval boundary.

Consequential actions must never execute directly from model output.

The successful workflow path becomes:

Research -> Risk -> Reviewer -> Approval -> Approved Action

Reviewer FAIL still terminates the workflow before approval.

Reviewer PASS may create a pending Approval and pause the workflow until an authorized Manager approves, rejects, or modifies the recommendation.

## Architectural Invariants

### Human decision is authoritative

The LLM may propose recommendations but cannot:

- approve its own recommendation;
- reject a recommendation;
- modify an authoritative recommendation;
- supply an approver identity;
- supply approval permissions;
- resume a Step Functions execution;
- execute a consequential action.

Only application code acting for an authenticated Principal with `Permission.APPROVAL_DECIDE` may decide an Approval.

### Segregation of duties

Approval decisions are Manager-only.

Existing RBAC remains authoritative:

- Manager: may read and decide approvals;
- Analyst: may not decide approvals;
- Admin: may read approvals but may not decide them.

Administrative platform authority is not compliance judgment authority.

### Three-way decision

Approval is not boolean.

Supported decisions are:

- `approved`;
- `rejected`;
- `modified`.

`modified` must carry a bounded replacement action/recommendation payload and a mandatory human justification.

`rejected` must carry a human justification.

All three decisions may carry an optional bounded human comment.

`approved` cannot silently alter the proposed action.

### Pending approval is immutable except through the decision service

A pending Approval represents the exact recommendation presented to the Manager.

The proposal payload, evidence references, deterministic score context, agent trace identifiers, model identifiers, workflow execution identifier, and task-token association must not be editable through general CRUD behavior.

The decision transition must be atomic and one-way.

A decided Approval cannot be decided again.

### Original Principal and tenant remain authoritative

`organization_id`, approver identity, role, and permissions always originate from the authenticated Principal.

No request body may supply or override:

- organization_id;
- approver_id;
- role;
- permissions;
- task token.

Repository reads and writes must remain tenant-scoped.

### Task tokens are secret capabilities

A Step Functions task token is an internal workflow capability.

It must:

- never be returned by approval list/read API responses;
- never be accepted from the Manager decision request body;
- never be logged;
- never be embedded in audit-event user-visible metadata;
- only be read by the trusted callback service after authorization and successful atomic decision persistence.

The Manager decides using the Approval ID. Application code resolves the associated internal task token.

### Decision persistence precedes workflow callback

The authoritative human decision must be durably recorded before attempting to resume Step Functions.

If the callback fails after persistence:

- the Approval remains decided;
- the decision must not be accepted a second time;
- callback retry/recovery must use the already-recorded decision;
- duplicate execution of a consequential action must be prevented.

### Execution is constrained to the approved action

Approval does not grant arbitrary execution rights.

The execution boundary may execute only:

- the originally proposed action for `approved`;
- no consequential action for `rejected`;
- the validated Manager-modified action for `modified`.

No model-generated action may bypass this projection.

## Approval State Model

The Phase 7 Approval aggregate has an explicit lifecycle.

### Status

- `pending`
- `decided`
- `execution_succeeded`
- `execution_failed`

Allowed transitions:

`pending -> decided`

`decided -> execution_succeeded`

`decided -> execution_failed`

No transition returns to `pending`.

No terminal execution state may transition again.

### Decision

Decision is nullable while status is `pending`.

Once decided it is exactly one of:

- `approved`
- `rejected`
- `modified`

A decision is immutable after persistence.

### Required approval context

A pending Approval must bind to:

- approval ID;
- organization ID;
- workflow execution ID;
- recommendation/action identifier;
- bounded proposed action payload;
- trusted evidence references;
- deterministic score and scoring version where applicable;
- agent execution / trace identifiers;
- generator and reviewer model identifiers where available;
- created timestamp;
- internal task-token association.

A decided Approval additionally binds to:

- decision;
- approver user ID;
- decision timestamp;
- mandatory justification for reject/modify;
- optional comment for approve;
- validated modified action when decision is `modified`.

## Phase 7 Implementation Plan

### Phase 7.1 - Approval Contracts and State Machine

Deliver:

- provider-neutral Approval models;
- explicit ApprovalStatus enum;
- reuse existing ApprovalDecision enum;
- bounded proposed-action contract;
- bounded modified-action contract;
- pending Approval contract;
- Manager decision request contract;
- decided Approval contract;
- explicit state-transition validation;
- no task-token field in public response models;
- unit tests for immutable and invalid transitions.

No database migration, HTTP endpoint, or AWS callback is added in 7.1.

Done when:

- pending approvals cannot contain a decision;
- decided approvals require decision and approver metadata;
- rejected and modified decisions require justification;
- modified decisions require a validated replacement action;
- approved decisions cannot smuggle a modified action;
- extra identity and task-token fields are rejected;
- contracts are frozen and bounded.

### Phase 7.2 - Approval Persistence and Queue

Deliver:

- Approval repository port;
- SQLAlchemy persistence model;
- new forward-only Alembic migration;
- tenant-scoped approval reads;
- pending approval creation;
- unique workflow/task-token association;
- atomic compare-and-set pending -> decided behavior;
- approval queue/list service;
- database constraints and indexes;
- integration tests for tenant isolation and duplicate decisions.

Task tokens remain internal-only.

Done when:

- Tenant A cannot read or decide Tenant B approvals;
- duplicate decision attempts fail closed;
- a task token cannot appear in public repository projections;
- database constraints enforce the lifecycle independently of API code.

### Phase 7.2 Persistence Design

Phase 7.2 uses one tenant-owned PostgreSQL `approvals` table.

The table is the durable authority for:

- the exact recommendation shown to the Manager;
- the internal Step Functions task-token association;
- the one-way human decision;
- later execution lifecycle state.

The public approval contracts remain separate from the persistence row.

#### Approval table

`approvals` contains:

- `id UUID PRIMARY KEY`;
- `organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE`;
- `workflow_execution_id VARCHAR(256) NOT NULL`;
- `recommendation_id UUID NOT NULL`;
- `proposed_action JSONB NOT NULL`;
- `evidence JSONB NOT NULL`;
- `score_context JSONB NULL`, containing the deterministic score, scoring version, and the exact `RiskScoringEngine` component breakdown when scoring applies;
- `agent_trace_ids JSONB NOT NULL`;
- `generator_model_id VARCHAR(256) NULL`;
- `reviewer_model_id VARCHAR(256) NULL`;
- `status approval_status NOT NULL`;
- `decision approval_decision NULL`;
- `approver_id UUID NULL`;
- `decided_at TIMESTAMPTZ NULL`;
- `justification TEXT NULL`;
- `comment TEXT NULL`;
- `modified_action JSONB NULL`;
- `task_token TEXT NOT NULL`;
- `created_at TIMESTAMPTZ NOT NULL`;
- `updated_at TIMESTAMPTZ NOT NULL`.

The database enum `approval_decision` mirrors the existing application enum:

- `approved`;
- `rejected`;
- `modified`.

The database enum `approval_status` mirrors the Phase 7 application enum:

- `pending`;
- `decided`;
- `execution_succeeded`;
- `execution_failed`.

#### Secret task-token boundary

`task_token` is persistence-internal data.

It must never be mapped into:

- `PendingApproval`;
- `DecidedApproval`;
- approval queue responses;
- approval detail responses;
- audit-event payloads;
- structured application logs.

The normal repository read methods return public/domain approval projections without the token.

A separate internal callback read method may return the task token only after the Approval is durably decided and while its lifecycle status remains `decided`. Once execution reaches `execution_succeeded` or `execution_failed`, callback context must no longer expose the task token.

The Manager API never accepts a task token.

The first Phase 7 implementation stores the task token only in the tenant-protected PostgreSQL row. Application/API secrecy and Aurora encryption-at-rest remain separate controls. Application-level token encryption is not introduced until there is an explicit key-management design rather than an ad-hoc cryptographic scheme.

#### Retention

Approval records are retained for at least seven years because they are part of the durable human-decision and audit record.

Phase 7.2 intentionally exposes no application DELETE path and the database mutation guard rejects ordinary row deletion. Automated archival or privileged retention cleanup is outside Phase 7.2 and must not weaken the seven-year minimum retention period or permit application-role deletion.

#### Uniqueness and indexes

The migration must enforce:

- unique `task_token`;
- unique `(organization_id, workflow_execution_id)`;
- unique `(organization_id, id)` on `users` if no equivalent candidate key already exists;
- composite foreign key `(organization_id, approver_id)` -> `users(organization_id, id)` with `ON DELETE RESTRICT`;
- index `(organization_id, id)`;
- pending queue index `(organization_id, created_at)` where `decision IS NULL`;
- decided lookup index `(organization_id, decision, decided_at)` where `decision IS NOT NULL`.

The tenant-coupled approver foreign key is intentional. A globally valid user UUID from another organization must not be recordable as the human approver even if application authorization is accidentally bypassed.

A workflow execution may create at most one Approval in this Phase 7 workflow.

#### Structural JSON constraints

The database must reject structurally invalid snapshots before application deserialization.

Required checks:

- `jsonb_typeof(proposed_action) = 'object'`;
- `jsonb_typeof(evidence) = 'array'`;
- `jsonb_array_length(evidence) <= 10`;
- `score_context IS NULL OR jsonb_typeof(score_context) = 'object'`;
- `jsonb_typeof(agent_trace_ids) = 'array'`;
- `jsonb_array_length(agent_trace_ids) <= 16`;
- `modified_action IS NULL OR jsonb_typeof(modified_action) = 'object'`.

Detailed field validation remains authoritative in the frozen Pydantic contracts.

#### Lifecycle constraints

A pending Approval must satisfy all of:

- `status = 'pending'`;
- `decision IS NULL`;
- `approver_id IS NULL`;
- `decided_at IS NULL`;
- `justification IS NULL`;
- `comment IS NULL`;
- `modified_action IS NULL`.

A decided Approval must satisfy:

- `status IN ('decided', 'execution_succeeded', 'execution_failed')`;
- `decision IS NOT NULL`;
- `approver_id IS NOT NULL`;
- `decided_at IS NOT NULL`.

Decision-specific constraints:

`approved`:

- `modified_action IS NULL`.

`rejected`:

- non-empty `justification` is required;
- `modified_action IS NULL`.

`modified`:

- non-empty `justification` is required;
- `modified_action IS NOT NULL`.

`comment` is optional for all three decisions and is not a substitute for the mandatory `justification` on rejected or modified decisions.

These rules must be represented as PostgreSQL CHECK constraints so a malformed repository call cannot bypass them.

#### Frozen recommendation context

After INSERT, these fields are immutable:

- `id`;
- `organization_id`;
- `workflow_execution_id`;
- `recommendation_id`;
- `proposed_action`;
- `evidence`;
- `score_context`;
- `agent_trace_ids`;
- `generator_model_id`;
- `reviewer_model_id`;
- `task_token`;
- `created_at`.

A database trigger must reject UPDATE attempts that alter any of those fields.

The trigger must also reject:

- `pending -> pending` writes that mutate decision fields;
- any transition back to `pending`;
- any second human decision;
- mutation of decision/approver/decision timestamp after decision;
- transition from either execution terminal state;
- DELETE.

The allowed lifecycle transitions remain exactly:

`pending -> decided`

`decided -> execution_succeeded`

`decided -> execution_failed`

Phase 7.2 implements only the atomic `pending -> decided` write path. Later execution-state changes reuse the same guarded lifecycle.

#### Row-level security

`approvals` is a tenant-owned table.

The migration must:

- `ENABLE ROW LEVEL SECURITY`;
- `FORCE ROW LEVEL SECURITY`;
- derive the predicate from `app.current_organization_id`;
- create tenant-scoped SELECT policy;
- create tenant-scoped INSERT policy;
- create tenant-scoped UPDATE policy;
- create no DELETE policy.

When the `cloudguard_app` role exists, grant only:

- `SELECT`;
- `INSERT`;
- `UPDATE`.

Do not grant DELETE.

Application queries still include explicit `organization_id` predicates as defense in depth.

#### Repository boundary

Follow the existing project persistence layout:

- repository port: `backend/app/ports/approval_repository.py`;
- SQLAlchemy implementation: `backend/app/adapters/local/approval_repository.py`;
- ORM `Approval` table mapping: `backend/app/repositories/tables.py`.

Do not introduce a second declarative base or a parallel ORM package.

Add an `ApprovalRepository` port with bounded methods equivalent to:

`create_pending(...) -> PendingApproval`

Creates the exact frozen recommendation snapshot and its internal task-token association.

`get_by_id(*, organization_id, approval_id) -> PendingApproval | DecidedApproval | None`

Always tenant scoped. Never returns a task token.

`list_pending(*, organization_id, limit) -> list[PendingApproval]`

Always tenant scoped.

`decide_pending(...) -> DecidedApproval | None`

Performs one atomic compare-and-set update equivalent to:

`UPDATE approvals ... WHERE organization_id = :organization_id AND id = :approval_id AND status = 'pending' AND decision IS NULL RETURNING ...`

Only the first decision may succeed.

No preliminary `SELECT` followed by an unconditional `UPDATE` is permitted for the authoritative transition.

`get_decided_callback_context(*, organization_id, approval_id) -> ApprovalCallbackContext | None`

Internal-only method.

It may return:

- approval ID;
- organization ID;
- immutable task token;
- recorded human decision;
- validated effective action information needed by the later callback adapter.

It must return nothing while the Approval is still pending or after the Approval reaches an execution terminal state. Callback retry/recovery is therefore possible only while the persisted lifecycle remains `decided`.

`ApprovalCallbackContext` is not an API response model.

#### Atomic decision semantics

The repository performs the human decision with a single compare-and-set UPDATE.

If the UPDATE returns no row, callers must distinguish only through a tenant-scoped follow-up read:

- no visible row -> not found;
- visible row already decided -> conflict.

A duplicate decision must never overwrite the original decision.

A retry after callback failure must use the already persisted decision rather than re-running the decision transition.

#### Persistence mapping

The SQLAlchemy persistence row is allowed to contain `task_token`.

Mapping from persistence to public/domain contracts must explicitly enumerate safe fields.

Do not implement public projections using:

- `row.__dict__`;
- unrestricted ORM serialization;
- generic `model_validate(row)` where internal fields could be copied accidentally.

The task token must be omitted by construction.

#### Migration

Create a new forward-only migration:

`0007_approvals.py`

with:

`revision = "0007"`

`down_revision = "0006"`

Do not modify historical migrations.

The migration must include:

- PostgreSQL enums;
- the `users(organization_id, id)` candidate key required by the tenant-coupled approver foreign key;
- approvals table;
- constraints;
- indexes;
- RLS;
- application-role privileges;
- guarded lifecycle/context trigger;
- downgrade cleanup in reverse dependency order, including removal of the added users candidate key only after the approvals foreign key/table is gone.

### Phase 7.3 - Manager Decision Service and API

Deliver:

- approval application service;
- Manager-only approve/reject/modify operations;
- approval list/detail endpoints;
- strict request schemas;
- server-derived approver and tenant identity;
- idempotent conflict behavior for already-decided approvals;
- API authorization matrix tests.

Do not invoke Step Functions from the HTTP route directly.

Done when:

- Manager can decide a pending Approval;
- Analyst receives 403;
- Admin receives 403 for decisions;
- forged tenant/approver/task-token fields are rejected;
- approval read responses never expose task tokens.

#### Phase 7.3 implementation status

Implemented locally on `phase-7-approvals`:

- `ApprovalService` for tenant-scoped pending queue, detail reads, and first-decision-wins decisions;
- `GET /api/v1/approvals`;
- `GET /api/v1/approvals/{approval_id}`;
- `POST /api/v1/approvals/{approval_id}/decision`;
- route-level and service-level `APPROVAL_READ` / `APPROVAL_DECIDE` enforcement;
- Manager-only decision authority;
- Admin read-only approval access;
- Analyst denied approval access;
- server-derived organization and approver identity;
- approved, rejected, and modified decision paths;
- conflict response for already-decided approvals and compare-and-set races;
- cross-tenant approval reads and decisions fail as not found;
- forged organization, approver, and task-token request fields are rejected;
- approval API responses never expose the internal task token;
- no Step Functions callback is invoked from the HTTP layer.

Focused Phase 7.3 validation currently passes:

- Ruff for affected Phase 7 files;
- mypy for the approval router/service path;
- 10 approval-service unit tests;
- 78 authenticated API authorization-matrix tests;
- 19 approval-persistence integration tests;
- 9 approval-API integration tests;
- `git diff --check`.

Full backend regression validation passes before commit:

- Ruff: all checks passed;
- mypy: no issues in 106 source files;
- pytest: 762 backend tests passed with PostgreSQL integration tests enabled;
- Alembic: `0009 (head)`;
- published migrations `0005`, `0006`, and `0007` remain unchanged;
- `git diff --check` passes.

#### Phase 7.2 privilege hardening follow-up

Published migration `0007_approvals.py` remains unchanged.

Forward migration `0008_approval_privilege_hardening.py` explicitly revokes
`DELETE` on `approvals` from `cloudguard_app` for existing environments that
may have inherited a prior broad table grant.

The approval persistence suite verifies with PostgreSQL
`has_table_privilege(current_user, 'approvals', 'DELETE')` that the runtime
application role does not have DELETE authority.

#### Phase 5 compliance privilege hardening follow-up

Published migrations `0005_compliance_scoring.py` and
`0006_score_overrides.py` remain unchanged.

Forward migration `0009_compliance_privilege_hardening.py` explicitly revokes
`UPDATE` and `DELETE` on `assessment_score_snapshots` and `score_overrides`
from `cloudguard_app` for existing environments that may have inherited prior
broad table grants.

The existing immutability triggers remain enabled, and the three regression
tests covering score-override mutation and assessment-snapshot update/delete
now pass under the runtime application role.

A direct PostgreSQL privilege regression also verifies that `cloudguard_app`
has neither `UPDATE` nor `DELETE` on `assessment_score_snapshots` or
`score_overrides`.

### Phase 7.4 - Step Functions Task-Token Pause and Callback

Deliver:

- Reviewer PASS -> Approval wait state;
- `.waitForTaskToken` integration;
- trusted approval creation worker;
- internal callback adapter/port;
- callback recovery semantics;
- Reviewer FAIL remains terminal;
- infrastructure tests for the deterministic graph;
- local callback test double.

The workflow must genuinely stop while approval is pending.

Done when:

- no callback occurs before a Manager decision;
- approved/modified decisions resume with validated decision data;
- rejected decisions resume into a non-executing rejection path;
- a forged or externally supplied task token cannot resume a workflow;
- callback retry cannot create a second human decision.

### Phase 7.5 - Audit and Approved-Action Boundary

Deliver:

- immutable audit events for approval creation, decision, callback, and execution result;
- approved-action projection;
- reject path that executes no consequential action;
- modified-action validation;
- execution idempotency key;
- failure recording;
- tests proving only the approved action can execute.

Automated remediation remains out of scope unless the approved action explicitly represents a bounded Phase 7 demo action.

Done when:

- every human decision records actor, decision, timestamp, and justification/comment;
- approval audit events contain no task token;
- rejection executes nothing;
- modification executes only the validated human-modified action;
- execution replay cannot duplicate a consequential side effect.

## Security Test Requirements

Phase 7 must explicitly test:

- Analyst decision attempt;
- Admin decision attempt;
- cross-tenant approval read;
- cross-tenant approval decision;
- model-supplied approver identity;
- request-supplied organization ID;
- request-supplied task token;
- task-token leakage in responses;
- task-token leakage in logs/audit payloads;
- duplicate decision race;
- decision of an already-decided Approval;
- reject without justification;
- modify without justification;
- modify without replacement action;
- approve with an unauthorized replacement action;
- callback before durable decision persistence;
- callback retry after persistence;
- duplicate action execution;
- Reviewer FAIL bypassing approval creation.

## Existing Components to Reuse

- `ApprovalDecision`
- `Permission.APPROVAL_READ`
- `Permission.APPROVAL_DECIDE`
- `Principal`
- centralized RBAC in `security/authz.py`
- Phase 6 Reviewer result
- Phase 6 workflow execution/correlation identifiers
- deterministic scoring models and scoring version
- existing repository/composition-root patterns
- AWS Step Functions/CDK infrastructure patterns

Do not duplicate these boundaries.

## Explicit Non-Goals

Phase 7 does not introduce:

- LLM-decided approvals;
- emergent agent delegation;
- model-visible AWS credentials;
- public task-token APIs;
- arbitrary tool execution after approval;
- approval by Admin;
- approval by Analyst;
- automated self-approval;
- unbounded remediation loops.

## Validation

Focused Phase 7 tests must pass before full validation.

Before each Phase 7 commit:

- Ruff passes for affected code;
- mypy passes for application code;
- focused Phase 7 tests pass;
- existing Phase 1-6 tests remain green;
- infrastructure tests pass when orchestration changes;
- `git diff --check` passes;
- architecture review confirms task-token secrecy and Manager-only decisions.

## Commit Policy

Do not commit or push until each subphase:

- has focused tests;
- preserves tenant isolation;
- preserves segregation of duties;
- passes applicable validation;
- has been reviewed for task-token leakage;
- does not permit a model to become the authoritative decision maker.
