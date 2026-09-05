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

`approved` may carry an optional bounded comment, but cannot silently alter the proposed action.

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
