#!/usr/bin/env python3
"""Reproducible CLI runner for Phase 9 AI & Security Evaluations.

Executes the versioned synthetic golden dataset (`evals/golden_dataset.jsonl`,
50 cases across 6 categories) and adversarial prompt-injection corpus
(`evals/adversarial.jsonl`, 8 cases) against the application's real
`RetrievalService`, `RAGService`, `AgentWorkflow`, `ToolRegistry`, and
`PromptIsolation` guards.

Modes:
- `deterministic_fixture` (default): Uses `InMemoryVectorStore` + deterministic
  feature embeddings (`eval:feature-hash-1024-v1`) + deterministic fixture LLM
  (`eval:fixture-llm-v1`). Zero external dependencies.
- `local_pgvector_fixture`: Seeds synthetic compliance corpus into PostgreSQL
  `documents` and `document_chunks` under tenant-scoped RLS (`SQLAlchemyVectorStore`)
  and optionally persists the resulting `EvaluationRun` into `evaluation_runs`.

Exit code:
- Returns 0 if `security_gate_passed` is True (and `--enforce-quality-gate` is not
  violated).
- Returns 1 if any security invariant fails (`cross_tenant_leak_count > 0`,
  `unauthorized_disclosure_count > 0`, `prompt_injection_breach_count > 0`,
  `unsafe_response_rate > 0`, or `adversarial_corpus_pass_rate < 1.0`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.adapters.local.directory import ACME as ACME_ORG_ID, LOCAL_USERS  # noqa: E402
from app.adapters.local.evaluation_repository import (  # noqa: E402
    SQLAlchemyEvaluationRepository,
)
from app.evaluations.harness import EvaluationHarness  # noqa: E402
from app.models.evaluation import (  # noqa: E402
    EvaluationExecutionMode,
    EvaluationRunCreate,
)
from app.repositories.database import (  # noqa: E402
    dispose_engine,
    tenant_session,
)


def _default_actor_for_org(organization_id: UUID) -> UUID:
    for user in LOCAL_USERS:
        if user.organization_id == organization_id and user.role == "manager":
            return user.user_id
    for user in LOCAL_USERS:
        if user.organization_id == organization_id:
            return user.user_id
    return UUID("00000000-0000-4000-8000-000000000102")


def _format_markdown_summary(run: EvaluationRunCreate) -> str:
    m = run.metrics_summary
    d = m.denominators
    lines = [
        "# CloudGuard AI - Phase 9 Evaluation Report",
        "",
        f"- **Dataset Version:** `{run.dataset_version}`",
        f"- **Execution Mode:** `{run.execution_mode.value}`",
        f"- **Embedding Model:** `{run.embedding_model_id}`",
        f"- **Generation Model:** `{run.generation_model_id}`",
        f"- **Reviewer / Judge Model:** `{run.reviewer_model_id}`",
        f"- **Security Gate (Blocking):** `{'PASS' if run.security_gate_passed else 'FAIL'}`",
        f"- **Quality Gate (Advisory):** `{'PASS' if run.quality_gate_advisory_passed else 'WARN'}`",
        "",
        "## Headline Metrics",
        "",
        "| Metric | Value | Denominator / Scope |",
        "| --- | --- | --- |",
        f"| `precision@5` | `{m.precision_at_5:.4f}` | `{d.retrieval_evaluated_cases}` retrieval cases |",
        f"| `recall@10` | `{m.recall_at_10:.4f}` | `{d.retrieval_evaluated_cases}` retrieval cases |",
        f"| `context_relevance_score` | `{m.context_relevance_score:.4f}` | `{d.retrieval_evaluated_cases}` retrieval cases |",
        f"| `groundedness_score` | `{m.groundedness_score:.4f}` | `{d.groundedness_evaluated_cases}` groundedness cases |",
        f"| `answer_relevance_score` | `{m.answer_relevance_score:.4f}` | `{d.answer_evaluated_cases}` answer cases |",
        f"| `citation_accuracy` | `{m.citation_accuracy:.4f}` | `{d.citation_evaluated_cases}` citation cases |",
        f"| `hallucination_rate` | `{m.hallucination_rate:.4f}` | `{d.groundedness_evaluated_cases}` groundedness cases |",
        f"| `unanswerable_abstention_rate` | `{m.unanswerable_abstention_rate:.4f}` | `{d.abstention_evaluated_cases}` unanswerable/ambiguous cases |",
        f"| `unsafe_response_rate` | `{m.unsafe_response_rate:.4f}` | `{d.total_cases}` total golden cases |",
        f"| `cross_tenant_leak_count` | `{m.cross_tenant_leak_count}` | `{d.total_cases}` total golden cases |",
        f"| `unauthorized_disclosure_count` | `{m.unauthorized_disclosure_count}` | `{d.total_cases}` total golden cases |",
        f"| `prompt_injection_breach_count` | `{m.prompt_injection_breach_count}` | `{d.prompt_injection_evaluated_cases}` injection + adversarial cases |",
        f"| `adversarial_corpus_pass_rate` | `{m.adversarial_corpus_pass_rate:.4f}` | `{d.prompt_injection_evaluated_cases}` injection + adversarial cases |",
        f"| `tool_call_success_rate` | `{m.tool_call_success_rate:.4f}` | `{d.tool_call_evaluated_count}` tool calls |",
        f"| `agent_task_completion_rate` | `{m.agent_task_completion_rate:.4f}` | `{d.agent_evaluated_cases}` agent workflow cases |",
        f"| `retrieval_latency_p50_ms` | `{m.retrieval_latency_p50_ms:.3f} ms` | `{d.total_cases}` cases |",
        f"| `retrieval_latency_p95_ms` | `{m.retrieval_latency_p95_ms:.3f} ms` | `{d.total_cases}` cases |",
        f"| `end_to_end_latency_p50_ms` | `{m.end_to_end_latency_p50_ms:.3f} ms` | `{d.total_cases}` cases |",
        f"| `end_to_end_latency_p95_ms` | `{m.end_to_end_latency_p95_ms:.3f} ms` | `{d.total_cases}` cases |",
        f"| `cost_per_1000_queries_usd` | `{m.cost_per_1000_queries_usd}` | Unmeasured in offline/fixture mode |",
        "",
        "## Category Breakdown",
        "",
        "| Category | Cases | Passed | Pass Rate | Avg P@5 | Avg R@10 | Groundedness | Citation Acc | Unsafe Rate |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for cat in run.category_breakdown:
        p5 = f"{cat.avg_precision_at_5:.4f}" if cat.avg_precision_at_5 is not None else "N/A"
        r10 = f"{cat.avg_recall_at_10:.4f}" if cat.avg_recall_at_10 is not None else "N/A"
        gr = (
            f"{cat.avg_groundedness_score:.4f}"
            if cat.avg_groundedness_score is not None
            else "N/A"
        )
        cit = (
            f"{cat.avg_citation_accuracy:.4f}"
            if cat.avg_citation_accuracy is not None
            else "N/A"
        )
        lines.append(
            f"| `{cat.category.value}` | {cat.case_count} | {cat.passed_count} | "
            f"`{cat.pass_rate:.4f}` | `{p5}` | `{r10}` | `{gr}` | `{cit}` | "
            f"`{cat.unsafe_response_rate:.4f}` |"
        )

    lines.extend(["", "## Provenance & Limitations", ""])
    for note in run.provenance_notes:
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


async def _run_cli(args: argparse.Namespace) -> int:
    mode = EvaluationExecutionMode(args.mode)
    org_id = UUID(args.organization_id)
    actor_id = (
        UUID(args.triggered_by)
        if args.triggered_by
        else _default_actor_for_org(org_id)
    )

    harness = EvaluationHarness(execution_mode=mode)
    run_payload = await harness.run_suite(
        organization_id=org_id,
        triggered_by=actor_id,
        max_cases=args.max_cases,
    )

    persisted_id: str | None = None
    if args.persist:
        async with tenant_session(org_id) as session:
            repo = SQLAlchemyEvaluationRepository(session)
            saved = await repo.create_run(run_payload)
            persisted_id = str(saved.id)

    if mode == EvaluationExecutionMode.LOCAL_PGVECTOR_FIXTURE or args.persist:
        await dispose_engine()

    markdown_report = _format_markdown_summary(run_payload)
    print(markdown_report)
    if persisted_id is not None:
        print(f"Persisted EvaluationRun id={persisted_id} for organization={org_id}")

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(run_payload.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    if args.output_markdown:
        md_path = Path(args.output_markdown)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(markdown_report, encoding="utf-8")

    if not run_payload.security_gate_passed:
        print(
            "ERROR: Evaluation security gate FAILED (blocking).",
            file=sys.stderr,
        )
        return 1

    if args.enforce_quality_gate and not run_payload.quality_gate_advisory_passed:
        print(
            "ERROR: Evaluation quality gate FAILED (--enforce-quality-gate enabled).",
            file=sys.stderr,
        )
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run CloudGuard AI Phase 9 evaluation suite."
    )
    parser.add_argument(
        "--mode",
        choices=[
            EvaluationExecutionMode.DETERMINISTIC_FIXTURE.value,
            EvaluationExecutionMode.LOCAL_PGVECTOR_FIXTURE.value,
        ],
        default=EvaluationExecutionMode.DETERMINISTIC_FIXTURE.value,
        help="Execution mode (default: deterministic_fixture).",
    )
    parser.add_argument(
        "--organization-id",
        default=str(ACME_ORG_ID),
        help="Tenant UUID to associate with the evaluation run.",
    )
    parser.add_argument(
        "--triggered-by",
        default=None,
        help="User UUID triggering the evaluation run.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Optional cap on the number of golden cases to execute.",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist the evaluation run into PostgreSQL evaluation_runs.",
    )
    parser.add_argument(
        "--enforce-quality-gate",
        action="store_true",
        help="Exit non-zero if advisory quality thresholds are not met.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional file path to write JSON evaluation output.",
    )
    parser.add_argument(
        "--output-markdown",
        default=None,
        help="Optional file path to write Markdown evaluation summary.",
    )
    args = parser.parse_args()
    return asyncio.run(_run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
