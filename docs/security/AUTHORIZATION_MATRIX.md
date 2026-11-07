# Authorization and Security Matrix

- **Reviewed:** 2026-09-07
- **Application source:** `backend/app/security/authz.py`
- **Permission regression:** `backend/tests/unit/test_authz.py`
- **Route regression:** `backend/tests/integration/test_authenticated_api.py`

This document is a human-readable mirror. The Python permission matrix remains
authoritative, and tests must fail if code and declared expectations diverge.

## Roles and clearance

Permissions answer **what operation** a caller may perform. Clearance answers
**which confidentiality levels** may enter a read or retrieval result. They are
independent controls.

| Role | Maximum clearance | Intended authority |
|---|---|---|
| Analyst | Internal | Upload and read documents, query AI, create/evaluate compliance work |
| Manager | Confidential | Analyst authority plus review, score override, investigation closure, and approval decisions |
| Admin | Restricted | Platform administration and broad read access, without compliance authoring or approval decisions |

Clearance is derived from the verified role. A request cannot ask for a higher
level. Admin's Restricted clearance does not make Admin an approval decider.

## Permission matrix

| Permission | Analyst | Manager | Admin |
|---|:---:|:---:|:---:|
| `document:upload` | Yes | Yes | No |
| `document:read` | Yes | Yes | Yes |
| `document:delete` | No | No | Yes |
| `ai:query` | Yes | Yes | No |
| `risk:read` | Yes | Yes | Yes |
| `risk:review` | No | Yes | No |
| `risk:modify_severity` | No | Yes | No |
| `compliance:create` | Yes | Yes | No |
| `investigation:create` | Yes | Yes | No |
| `investigation:read` | Yes | Yes | Yes |
| `investigation:close` | No | Yes | No |
| `approval:read` | No | Yes | Yes |
| `approval:decide` | No | Yes | No |
| `analytics:read_own` | Yes | Yes | No |
| `analytics:read_team` | No | Yes | Yes |
| `user:manage` | No | No | Yes |
| `settings:manage` | No | No | Yes |
| `audit:read` | No | No | Yes |

There is no wildcard permission and no implicit Admin inheritance. A new
permission must be added deliberately to each role's expected set.

## Public HTTP routes

| Method | Route | Exposure | Notes |
|---|---|---|---|
| GET | `/api/v1/health` | Public | Liveness only; no credentials or database data |
| GET | `/api/v1/health/ready` | Public | Readiness status; must not expose connection details |
| GET | `/api/v1/auth/config` | Public | Login discovery only; no secret values |
| POST | `/api/v1/auth/dev-login` | Local only | Route is absent outside `ENVIRONMENT=local` |

All other current API routes require a verified bearer token. Production OpenAPI
is disabled by default.

## Protected route matrix

`Allowed` means the role passes the route permission gate. The request can still
receive validation, not-found, conflict, or upstream errors after authorization.

| Method | Route | Required permission(s) | Analyst | Manager | Admin |
|---|---|---|:---:|:---:|:---:|
| GET | `/api/v1/me` | Authenticated Principal | Allowed | Allowed | Allowed |
| GET | `/api/v1/system/config` | `settings:manage` | Denied | Denied | Allowed |
| POST | `/api/v1/documents` | `document:upload` | Allowed | Allowed | Denied |
| POST | `/api/v1/documents/{document_id}/complete` | `document:upload` | Allowed | Allowed | Denied |
| GET | `/api/v1/documents` | `document:read` | Allowed | Allowed | Allowed |
| GET | `/api/v1/documents/{document_id}` | `document:read` | Allowed | Allowed | Allowed |
| POST | `/api/v1/retrieval/search` | `document:read` | Allowed | Allowed | Allowed |
| POST | `/api/v1/rag/query` | `document:read` and `ai:query` | Allowed | Allowed | Denied |
| POST | `/api/v1/compliance/assessments` | `compliance:create` | Allowed | Allowed | Denied |
| GET | `/api/v1/compliance/assessments` | `risk:read` | Allowed | Allowed | Allowed |
| GET | `/api/v1/compliance/assessments/{assessment_id}` | `risk:read` | Allowed | Allowed | Allowed |
| PATCH | `/api/v1/compliance/control-assessments/{control_assessment_id}` | `compliance:create` | Allowed | Allowed | Denied |
| GET | `/api/v1/compliance/control-assessments/{control_assessment_id}` | `risk:read` | Allowed | Allowed | Allowed |
| POST | `/api/v1/compliance/control-assessments/{control_assessment_id}/evidence` | `compliance:create` | Allowed | Allowed | Denied |
| POST | `/api/v1/compliance/assessments/{assessment_id}/compute` | `compliance:create` | Allowed | Allowed | Denied |
| POST | `/api/v1/compliance/assessments/{assessment_id}/candidates` | `compliance:create` | Allowed | Allowed | Denied |
| POST | `/api/v1/compliance/assessments/{assessment_id}/finalize` | `risk:review` | Denied | Allowed | Denied |
| POST | `/api/v1/compliance/assessments/{assessment_id}/override` | `risk:modify_severity` | Denied | Allowed | Denied |
| GET | `/api/v1/compliance/assessments/{assessment_id}/snapshots` | `risk:read` | Allowed | Allowed | Allowed |
| GET | `/api/v1/compliance/assessments/{assessment_id}/overrides` | `risk:read` | Allowed | Allowed | Allowed |
| GET | `/api/v1/approvals` | `approval:read` | Denied | Allowed | Allowed |
| GET | `/api/v1/approvals/{approval_id}` | `approval:read` | Denied | Allowed | Allowed |
| POST | `/api/v1/approvals/{approval_id}/decision` | `approval:decide` | Denied | Allowed | Denied |

Requiring `ai:query` as well as document read for RAG is intentional. Raw retrieval
and model disclosure are different operations. In particular, Admin may inspect a
Restricted document but cannot send its content to a model through the RAG route.

## Tenant and object authorization

Passing a route gate is not sufficient to access an object:

1. `organization_id` and actor ID come from the verified token-derived `Principal`.
2. A tenant transaction sets `app.current_organization_id`.
3. Repository queries include explicit organization predicates.
4. Forced RLS refuses rows from other tenants even if a query omits its predicate.
5. Document retrieval also applies the role-derived confidentiality set.
6. Cross-tenant object IDs normally appear as not found rather than confirming that
   another tenant owns the object.

Request bodies must not accept authoritative `organization_id`, `approver_id`, role,
permission, clearance, or task-token fields. Strict Pydantic models reject extras at
the relevant boundaries.

## Agent and tool authority

| Component | Permitted application capability | Model authority |
|---|---|---|
| Research Agent | One bounded `search_documents` call | Proposes strict search arguments only |
| Compliance Agent | Read policy plus bounded document search | Proposes search and advisory findings only |
| Risk Agent | No tools | Advisory likelihood/impact estimates only |
| Reviewer Agent | No tools | PASS/FAIL review only; cannot approve or execute |
| Tool Registry | `search_documents`; `get_policy` for Compliance | None; registry validates allowlist, schema, Principal, and budget |
| Recommendation Builder | One application-owned `create_security_review` proposal | None; recommendation is deterministic server construction |
| Approval API | Manager decision through `approval:decide` | None |
| Approved Action Worker | Reload and execute one persisted allowlisted action | None; workflow input supplies only execution ID and Principal |

## Background and AWS capability matrix

| Workload | Data/model capability | Explicitly excluded authority |
|---|---|---|
| Document processor | Read tenant S3 object, use embeddings, write tenant chunks/vectors/status | Human approval, general document mutation, arbitrary S3 keys |
| Research worker | Invoke configured chat/embedding models; tenant-scoped vector read | Writes, approval, callback, action execution |
| Risk worker | Invoke configured chat model | Database, tools, approval, action execution |
| Reviewer worker | Invoke independently configured judge model | Database, tools, approval, action execution |
| Approval creation worker | Create exact pending approval in tenant transaction | Human decision and callback |
| Callback recovery worker | Claim decided callbacks and call `states:SendTaskSuccess` | Approval decision and consequential action |
| Approved-action worker | Tenant transaction, persisted approval reload, bounded security-review insert | Model/workflow-supplied action authority |

`states:SendTaskSuccess` uses IAM resource `*` because task tokens do not support
resource-level ARN scoping. The permission is therefore isolated to the callback
recovery function. Deployment must separately restrict who can start the state
machine because workers trust the carried Principal only inside that boundary.

## Change procedure

Any new role, permission, route, tool, worker, approval action, or tenant table must:

1. update the authoritative Python matrix or bounded policy;
2. update exhaustive unit/API expectations;
3. add negative tests for every role or caller class that must be denied;
4. preserve server-derived tenant and actor authority;
5. add RLS and runtime-role privilege tests for new tenant data; and
6. update this matrix and the threat register in the same change.
