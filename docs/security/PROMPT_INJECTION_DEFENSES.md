# Prompt-Injection Defenses

CloudGuard AI treats every user request, retrieved document, policy excerpt, evidence
excerpt, and upstream model rationale as untrusted data. A model never supplies
identity, tenant, role, clearance, permission, tool availability, approval, write
authority, or an authoritative score.

## Input boundary

All seven generation paths use `app.security.prompt_isolation` to construct model
input. The helper:

1. prepends a shared application-authored authority policy to each task policy;
2. emits application-selected identifiers as a separate canonical JSON object;
3. places the user request and each retrieved or upstream-model input in separately
   named untrusted sections;
4. creates a fresh 128-bit random boundary nonce for every prompt; and
5. retries and then fails closed if a generated nonce occurs in untrusted input.

The nonce prevents content prepared before a request from predicting the real
closing marker. Marker-like text supplied by a user or document remains inside the
untrusted section. This structural defense does not make model prose immune to
adversarial influence.

## Output and authority boundary

Application code, rather than model instructions, enforces authority:

- strict Pydantic schemas reject unknown tools and malformed output;
- static per-agent allowlists constrain tool selection and arguments;
- source, chunk, evidence, document, framework, and control identifiers must belong
  to the application-admitted set;
- an explicit control scope cannot be empty, while an omitted scope still requires
  every returned control identifier to exist in the application-owned catalogue;
- deterministic Python scoring remains authoritative; and
- approval and write execution still require persisted human authorization and
  application-owned action policy.

The reusable synthetic corpus at
`backend/tests/fixtures/security/prompt_injection.json` covers attempted role,
tenant, clearance, tool, approval, write-authority, score, and delimiter escalation.
It is a regression baseline and must expand when prompts, models, tools, or attack
patterns change.

## Bedrock Guardrail behavior

`BEDROCK_GUARDRAIL_ID` and `BEDROCK_GUARDRAIL_VERSION` form one required pair.
Partial configuration is rejected, and production API and agent-worker settings
require the pair at startup. The Bedrock adapter sends the pair with each Converse
request. A `guardrail_intervened` stop reason or any non-empty/non-`NONE` Guardrail
action causes a sanitized upstream failure; blocked content is not returned to a
service.

The repository does not provision the Guardrail resource in CDK. Each deployed
environment must provision and version it separately, then inject both values.
Guardrails are provider defense in depth and do not replace local authorization,
tenant isolation, clearance filtering, output validation, or human approval.

## Residual risk

A sufficiently effective injection can still influence non-authoritative generated
prose. PII classification, model-context minimization, and output-sensitive-data
controls remain Phase 8.3 work. Operational evaluation should monitor false
positives, false negatives, model changes, and new injection patterns without
logging prompts or document bodies.
