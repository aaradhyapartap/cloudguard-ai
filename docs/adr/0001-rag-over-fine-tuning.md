# ADR-0001: Retrieval-augmented generation rather than fine-tuning

- **Status:** accepted
- **Date:** 2026-08-22
- **Phase:** 0

## Context

The platform must answer questions about an organisation's own governance
corpus. Two ways to give a model that knowledge: fine-tune on the corpus, or
retrieve from it at query time.

Three properties are non-negotiable for a compliance product:

1. Every material claim must cite a specific current document, page and section.
2. Retrieval must be filtered by the caller's tenant and clearance.
3. A document that is deleted or superseded must stop influencing answers
   immediately.

## Options

| Option | Pros | Cons |
|---|---|---|
| Fine-tuning | Lower per-query token cost; learns domain register | Knowledge is baked into weights: uncitable, unfilterable per user, and stale until the next training run. A deletion request cannot be honoured without retraining. |
| RAG | Citable, filterable, current, auditable. Corpus changes take effect immediately. | Higher per-query cost; retrieval quality becomes its own engineering problem |
| Both | Register from tuning, facts from retrieval | Cost and complexity unjustified at this scale |

## Decision

RAG. Fine-tuning is out of scope for the whole project.

## Consequences

Retrieval quality is now the dominant driver of answer quality, which is why
hybrid retrieval is in the MVP (ADR-0007) and why the Phase 9 evaluation harness
is not optional. Per-query cost is higher, mitigated by prompt caching and by
routing simple tasks to cheaper models.

The trade accepted: the model will not pick up the organisation's house style
or domain register. For this product that is fine — compliance findings should
read in a consistent neutral register, not in any one organisation's voice.

## Revisit when

Never for facts. Revisit for *format* only if structured-output constraints
prove insufficient to get consistently shaped findings.
