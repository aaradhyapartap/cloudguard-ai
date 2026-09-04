# Current Development Task

## Phase

Phase 6 - Deterministic Agent Workflows

## Branch

phase-6-agents

## Objective

Implement CloudGuard AI's first bounded multi-agent workflow using fixed, auditable orchestration rather than emergent agent-to-agent delegation.

Phase 6 implements:

Research -> Risk -> Reviewer

with a Principal-bound ToolRegistry and deterministic workflow graph.

Human approval, task tokens, approval decisions, and execution of approved actions remain Phase 7 and are explicitly out of scope.

## Architectural Invariants

### Original human Principal is authoritative

Every agent execution and every tool invocation carries the original authenticated Principal.

Agents do not receive elevated service identities for application authorization.

Tenant identity, role permissions, and confidentiality clearance must never be supplied by model output or request payloads.

Existing application services remain authoritative for their own security boundaries.

### ToolRegistry is the execution gate

The model may propose a tool-call intent, but it never receives credentials or directly invokes infrastructure.

A tool call executes only when both conditions pass:

1. the requested tool exists in the registry and is statically allowed for that agent; and
2. the original human Principal is authorized for the underlying operation.

Unknown tools, malformed arguments, out-of-scope tools, and unauthorized calls fail closed before tool code executes.

No wildcard tool permissions are allowed.

### Agents have narrow static capabilities

Research Agent:

- may search authorized documents and obtain retrieved evidence;
- is strictly read-only;
- cannot mutate compliance, risk, investigation, approval, or document state.

Risk Agent:

- consumes validated evidence/context produced by prior bounded steps;
- emits structured candidate risk/component estimates only;
- cannot write authoritative scores;
- deterministic Python scoring remains authoritative.

Reviewer Agent:

- consumes the bounded workflow result and authoritative retrieved evidence;
- validates grounding, citation existence, schema integrity, and workflow constraints;
- has no write tools;
- may fail the workflow;
- must use a separately configured reviewer/judge model rather than silently reusing the generator model configuration.

### LLMs remain advisory

No model output directly becomes an authoritative compliance score, risk classification, approval decision, or executed action.

Structured model output is parsed and validated by application code.

Python remains authoritative for deterministic risk scoring.

### Retrieval security is not duplicated

Agent document search delegates to the existing RetrievalService.

RetrievalService remains authoritative for:

- organization scoping from Principal.organization_id;
- confidentiality filtering from the Principal clearance ceiling;
- vector search tenant isolation.

The ToolRegistry authorizes whether an agent may request retrieval; it does not replace retrieval security.

### Orchestration is deterministic

The workflow graph is fixed:

Research -> Risk -> Reviewer

Agents cannot dynamically choose another agent, spawn arbitrary agents, or change workflow topology.

Reviewer failure terminates the Phase 6 workflow.

Phase 7 will extend the successful Reviewer path with human approval.

### Provider-neutral model access

Agents depend only on the existing LLMProvider port and provider-neutral GenerationRequest / GenerationResponse models.

No agent service imports Bedrock SDK types.

The composition root is responsible for selecting concrete providers.

Phase 6 may wire distinct generator and reviewer LLMProvider instances so the reviewer model can be configured independently.

### Bounded execution

Every model invocation must have explicit bounds for:

- input/context size;
- output tokens;
- number of retrieved evidence items;
- number of tool calls;
- tool argument schema;
- structured output schema.

No unbounded agent loop is permitted.

### Phase 7 boundary

Phase 6 does NOT implement:

- approval queue behavior;
- waitForTaskToken;
- Manager approval/rejection/modification callbacks;
- execution of approved actions;
- automated remediation.

Those remain Phase 7.

## Phase 6 Implementation Plan

### Phase 6.1 - Agent Contracts and Secure ToolRegistry

Deliver:

- provider-neutral agent/domain models;
- bounded AgentType identity;
- typed tool-call request and result contracts;
- explicit tool definitions with argument schemas;
- static per-agent allowlists;
- Principal-bound ToolRegistry;
- fail-closed unknown-tool behavior;
- fail-closed malformed argument behavior;
- authorization before execution;
- bounded tool-call count;
- unit tests proving out-of-scope tools never execute.

Initial tool surface should remain intentionally small.

Expected Research capability:

- search_documents

Additional read tools are added only when an implemented service can back them without bypassing existing authorization.

Done when:

- Research can invoke its explicitly allowed read tool;
- Risk and Reviewer cannot invoke Research-only tools unless explicitly granted;
- unknown and unauthorized calls are rejected before handler execution;
- tests prove the original Principal is passed to the underlying service.

### Phase 6.2 - Research Agent

Deliver:

- bounded Research Agent service;
- retrieval through the ToolRegistry and existing RetrievalService;
- deterministic evidence/source labels;
- structured Research result schema;
- preservation of trusted chunk/document provenance;
- prompt-injection boundary treating retrieved documents as untrusted data;
- zero-result behavior that avoids unnecessary model calls where possible;
- strict context and output ceilings.

Research remains read-only.

Done when:

- Research produces a validated bounded evidence result;
- every evidence reference maps to actually retrieved authorized evidence;
- higher-clearance and cross-tenant evidence cannot enter the result;
- prompt text cannot cause an out-of-scope tool to execute.

### Phase 6.3 - Risk Agent

Deliver:

- bounded Risk Agent service;
- structured candidate risk/component estimates derived only from trusted upstream evidence;
- evidence references constrained to trusted upstream Research output;
- no direct persistence of authoritative score/classification;
- integration with the deterministic Python scoring boundary where applicable;
- strict schema and finite numeric validation;
- fail-closed handling of malformed or invented evidence references.

Done when:

- identical accepted deterministic scoring inputs still produce identical authoritative scores independent of model output wording;
- Risk Agent can propose estimates but cannot write or override the authoritative deterministic result;
- invented evidence identifiers are rejected.

### Phase 6.4 - Reviewer Agent

Deliver:

- bounded Reviewer Agent service;
- separately configured reviewer/judge LLMProvider;
- grounding validation;
- citation/evidence existence validation;
- structured PASS / FAIL review result with bounded reasons;
- deterministic application checks around model review output;
- no write-capable tools.

Reviewer failure must fail the workflow.

Done when:

- a grounded valid run can pass;
- missing/invented citations fail;
- malformed reviewer output fails closed;
- Reviewer cannot mutate application state;
- tests prove the Reviewer provider can be configured independently from the generator provider.

### Phase 6.5 - Deterministic Workflow Orchestration

Deliver:

- fixed Research -> Risk -> Reviewer workflow contract;
- deterministic workflow state transitions;
- execution/correlation identifiers;
- bounded step inputs/outputs;
- failure propagation;
- Step Functions definition/infrastructure for the fixed graph;
- local/test orchestration path that does not require AWS;
- workflow trace metadata sufficient for later Phase 7 audit/approval use;
- ENABLE_AGENTIC_WORKFLOWS remains the feature gate.

No human approval state is added in Phase 6.

Done when:

- the fixed graph executes in order;
- no agent can dynamically select the next agent;
- Reviewer FAIL terminates the run;
- the ToolRegistry rejects an out-of-scope call;
- local deterministic tests and infrastructure tests cover the graph.

### Phase 6.6 - Bounded Compliance Agent

Deliver:

- bounded Compliance Agent service;
- Principal-bound `get_policy` and `search_documents` execution through the ToolRegistry;
- tenant-safe read-only compliance policy boundary;
- policy/control context bounded to at most 25 controls;
- evidence retrieval bounded to at most 10 chunks;
- model-planned search intent with strict schema validation;
- model evaluation constrained to trusted policy context and trusted retrieved evidence;
- non-authoritative candidate findings only;
- no score persistence, finding persistence, notifications, approvals, or remediation;
- source-label validation against actually retrieved evidence;
- quote provenance derived only from trusted retrieved chunk content;
- duplicate-control and unknown-source fail-closed validation;
- real planning/evaluation model usage telemetry;
- zero-evidence behavior that skips the evaluation model call;
- tests proving original tenant and clearance boundaries are preserved.

Compliance remains read-only and non-authoritative.

Done when:

- `get_policy` and `search_documents` are the only Compliance Agent tools;
- Research, Risk, and Reviewer cannot invoke `get_policy`;
- policy reads use the original human Principal and tenant-scoped assessment lookup;
- the model cannot supply organization, role, permission, or clearance identity;
- the evaluator receives trusted control title/description from the policy boundary;
- every returned evidence reference maps to retrieved authorized evidence;
- cross-tenant and higher-clearance evidence cannot enter findings;
- malformed tool intent, invented source labels, duplicate controls, and invalid output fail closed;
- no Phase 7 approval/task-token behavior is introduced;
- focused Compliance and ToolRegistry tests pass.
## Security Test Requirements

Phase 6 tests must explicitly cover:

- cross-tenant tool requests;
- confidentiality/clearance enforcement;
- out-of-allowlist tools;
- unknown tools;
- malformed tool arguments;
- model-supplied organization IDs being ignored/rejected;
- model-supplied role/clearance escalation attempts;
- prompt-injection attempts requesting write tools;
- invented evidence/chunk identifiers;
- Reviewer write attempts;
- Reviewer FAIL workflow termination;
- tool-call budget exhaustion;
- model output schema failures.

## Existing Components to Reuse

- LLMProvider
- GenerationRequest
- GenerationResponse
- RetrievalService
- VectorStore
- Principal
- centralized RBAC in security/authz.py
- existing compliance deterministic scoring services
- existing configuration/composition-root pattern
- existing AWS Step Functions/CDK infrastructure patterns

Do not duplicate these boundaries in agent-specific code.

## Validation

Focused Phase 6 validation must pass before full validation.

Full validation:

powershell -ExecutionPolicy Bypass -File .\scripts\validate-all.ps1

Additionally:

- Ruff must pass.
- mypy must pass.
- git diff --check must pass.
- existing Phase 1-5 tests must remain green.
- agent authorization matrix tests must pass.
- workflow infrastructure tests must pass.

## Commit Policy

Do not commit or push until:

- focused validation passes;
- full validation passes;
- git diff --check passes;
- architecture review is complete;
- Phase 7 approval behavior has not leaked into Phase 6.
