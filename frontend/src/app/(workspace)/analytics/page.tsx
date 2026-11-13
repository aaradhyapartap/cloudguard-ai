'use client';

import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '@/components/auth/AuthProvider';
import { ApiError, api } from '@/lib/api-client';
import type {
  EvaluationDatasetMetadata,
  EvaluationRunDetail,
  EvaluationRunListResponse,
  EvaluationRunSummary,
} from '@/lib/types';

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'N/A';
  return `${(value * 100).toFixed(1)}%`;
}

function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'N/A';
  return `${value.toFixed(1)} ms`;
}

export default function AnalyticsPage() {
  const { me, can } = useAuth();
  const [metadata, setMetadata] = useState<EvaluationDatasetMetadata | null>(null);
  const [runs, setRuns] = useState<EvaluationRunSummary[]>([]);
  const [visibilityScope, setVisibilityScope] = useState<'own' | 'team'>('own');
  const [selectedRun, setSelectedRun] = useState<EvaluationRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [runningEval, setRunningEval] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canReadAnalytics =
    can('analytics:read_own') || can('analytics:read_team');
  const canTriggerEvaluation =
    canReadAnalytics && can('document:read') && can('ai:query');

  const loadRunDetail = useCallback(async (runId: string) => {
    try {
      const detail = await api.get<EvaluationRunDetail>(
        `/evaluations/runs/${runId}`,
      );
      setSelectedRun(detail);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Unable to load evaluation detail.',
      );
    }
  }, []);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [datasetMeta, runList] = await Promise.all([
        api.get<EvaluationDatasetMetadata>('/evaluations/dataset'),
        api.get<EvaluationRunListResponse>('/evaluations/runs?limit=20'),
      ]);
      setMetadata(datasetMeta);
      setRuns(runList.items);
      setVisibilityScope(runList.visibility_scope);
      const firstRun = runList.items[0];
      if (firstRun) {
        await loadRunDetail(firstRun.id);
      } else {
        setSelectedRun(null);
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Failed to load evaluation analytics.',
      );
    } finally {
      setLoading(false);
    }
  }, [loadRunDetail]);

  useEffect(() => {
    if (canReadAnalytics) {
      void loadDashboard();
    } else {
      setLoading(false);
    }
  }, [canReadAnalytics, loadDashboard]);

  async function handleTriggerEvaluation() {
    if (!canTriggerEvaluation || runningEval) return;
    setRunningEval(true);
    setError(null);
    try {
      const created = await api.post<EvaluationRunDetail>('/evaluations/runs', {
        execution_mode: 'deterministic_fixture',
      });
      setSelectedRun(created);
      setRuns((prev) => [created, ...prev.filter((r) => r.id !== created.id)]);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Failed to execute evaluation run.',
      );
    } finally {
      setRunningEval(false);
    }
  }

  if (!canReadAnalytics) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <h1 className="text-xl font-semibold">Evaluation &amp; Analytics</h1>
        <p className="text-sm text-[var(--color-ink-muted)]">
          Your role does not grant access to evaluation analytics.
        </p>
      </div>
    );
  }

  const activeSummary = selectedRun ?? runs[0] ?? null;
  const metrics = activeSummary?.metrics_summary ?? null;

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--color-line)] pb-5">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-accent)]">
            AI Quality &amp; Security Harness
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-[var(--color-ink)]">
            Evaluation &amp; Analytics
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--color-ink-muted)]">
            Reproducible retrieval, grounding, citation, agent-completion, and
            adversarial security metrics scoped to tenant{' '}
            <span className="font-mono text-xs">{me?.organization_id}</span> (
            <span className="font-mono text-xs">
              visibility: {visibilityScope}
            </span>
            ).
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canTriggerEvaluation ? (
            <>
              <span
                className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface)] px-2.5 py-1 font-mono text-[11px] text-[var(--color-ink-muted)]"
                title="Only the in-memory deterministic fixture mode may be triggered from the dashboard; pgvector and live modes are operator/CI only."
              >
                mode: deterministic_fixture
              </span>
              <button
                type="button"
                onClick={() => void handleTriggerEvaluation()}
                disabled={runningEval}
                className="rounded-[var(--radius-sm)] bg-[var(--color-accent)] px-3.5 py-1.5 text-xs font-medium text-white hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
              >
                {runningEval ? 'Running Suite…' : 'Run Evaluation Suite'}
              </button>
            </>
          ) : (
            <span className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface)] px-2.5 py-1 font-mono text-[11px] text-[var(--color-ink-muted)]">
              Read-only ({me?.role}: ai:query not granted)
            </span>
          )}
        </div>
      </header>

      {error && (
        <div
          role="alert"
          className="border border-l-[3px] border-[var(--color-line)] border-l-[var(--color-severity-critical)] bg-[var(--color-surface)] px-4 py-3 text-sm"
        >
          {error}
        </div>
      )}

      {metadata && (
        <section aria-labelledby="corpus-heading" className="space-y-2">
          <h2 id="corpus-heading" className="text-sm font-medium">
            Dataset &amp; Corpus Inventory
          </h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="border border-[var(--color-line)] bg-[var(--color-surface)] p-3">
              <p className="text-xs text-[var(--color-ink-muted)]">
                Dataset Version
              </p>
              <p className="mt-1 font-mono text-base font-semibold">
                v{metadata.dataset_version}
              </p>
            </div>
            <div className="border border-[var(--color-line)] bg-[var(--color-surface)] p-3">
              <p className="text-xs text-[var(--color-ink-muted)]">
                Golden Cases
              </p>
              <p className="mt-1 font-mono text-base font-semibold">
                {metadata.golden_case_count}
              </p>
            </div>
            <div className="border border-[var(--color-line)] bg-[var(--color-surface)] p-3">
              <p className="text-xs text-[var(--color-ink-muted)]">
                Adversarial Cases
              </p>
              <p className="mt-1 font-mono text-base font-semibold">
                {metadata.adversarial_case_count}
              </p>
            </div>
            <div className="border border-[var(--color-line)] bg-[var(--color-surface)] p-3">
              <p className="text-xs text-[var(--color-ink-muted)]">
                Scenario Categories
              </p>
              <p className="mt-1 font-mono text-base font-semibold">
                {Object.keys(metadata.category_counts).length}
              </p>
            </div>
          </div>
        </section>
      )}

      {loading ? (
        <p className="text-sm text-[var(--color-ink-muted)]">
          Loading evaluation telemetry…
        </p>
      ) : !activeSummary || !metrics ? (
        <div className="border border-[var(--color-line)] bg-[var(--color-surface)] p-6 text-sm text-[var(--color-ink-muted)]">
          No evaluation runs recorded for this visibility scope yet.{' '}
          {canTriggerEvaluation
            ? 'Click "Run Evaluation Suite" above to execute the 50-case golden dataset.'
            : ''}
        </div>
      ) : (
        <>
          <section aria-labelledby="provenance-heading" className="space-y-2">
            <h2 id="provenance-heading" className="text-sm font-medium">
              Execution Provenance &amp; Gate Status
            </h2>
            <div className="border border-[var(--color-line)] bg-[var(--color-surface)] p-4 text-xs">
              <div className="flex flex-wrap items-center gap-4">
                <span>
                  Mode:{' '}
                  <strong className="font-mono">
                    {activeSummary.execution_mode}
                  </strong>
                </span>
                <span>
                  Security Gate (Blocking):{' '}
                  <strong className="font-mono">
                    {activeSummary.security_gate_passed ? 'PASS' : 'FAIL'}
                  </strong>
                </span>
                <span>
                  Quality Gate (Advisory):{' '}
                  <strong className="font-mono">
                    {activeSummary.quality_gate_advisory_passed ? 'PASS' : 'WARN'}
                  </strong>
                </span>
                <span>
                  Generation Model:{' '}
                  <strong className="font-mono">
                    {activeSummary.generation_model_id}
                  </strong>
                </span>
                <span>
                  Reviewer/Judge:{' '}
                  <strong className="font-mono">
                    {activeSummary.reviewer_model_id}
                  </strong>
                </span>
              </div>
              <ul className="mt-3 list-disc space-y-1 pl-5 text-[var(--color-ink-muted)]">
                {activeSummary.provenance_notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          </section>

          <section aria-labelledby="headline-metrics-heading" className="space-y-2">
            <h2 id="headline-metrics-heading" className="text-sm font-medium">
              Headline Retrieval, Grounding, Safety &amp; Latency Metrics
            </h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                [
                  'Precision@5',
                  formatPercent(metrics.precision_at_5),
                  `${metrics.denominators.retrieval_evaluated_cases} retrieval cases`,
                ],
                [
                  'Recall@10',
                  formatPercent(metrics.recall_at_10),
                  `${metrics.denominators.retrieval_evaluated_cases} retrieval cases`,
                ],
                [
                  'Groundedness',
                  formatPercent(metrics.groundedness_score),
                  `${metrics.denominators.groundedness_evaluated_cases} groundedness cases`,
                ],
                [
                  'Citation Accuracy',
                  formatPercent(metrics.citation_accuracy),
                  `${metrics.denominators.citation_evaluated_cases} citation cases`,
                ],
                [
                  'Answer Relevance',
                  formatPercent(metrics.answer_relevance_score),
                  `${metrics.denominators.answer_evaluated_cases} answer cases`,
                ],
                [
                  'Hallucination Rate',
                  formatPercent(metrics.hallucination_rate),
                  `${metrics.denominators.groundedness_evaluated_cases} groundedness cases`,
                ],
                [
                  'Abstention Rate',
                  formatPercent(metrics.unanswerable_abstention_rate),
                  `${metrics.denominators.abstention_evaluated_cases} unanswerable cases`,
                ],
                [
                  'Unsafe Response Rate',
                  formatPercent(metrics.unsafe_response_rate),
                  `Leaks: ${metrics.cross_tenant_leak_count} · Breaches: ${metrics.prompt_injection_breach_count}`,
                ],
                [
                  'Adversarial Pass Rate',
                  formatPercent(metrics.adversarial_corpus_pass_rate),
                  `${metrics.denominators.prompt_injection_evaluated_cases} injection/adversarial cases`,
                ],
                [
                  'Agent Task Completion',
                  formatPercent(metrics.agent_task_completion_rate),
                  `Tool success: ${formatPercent(metrics.tool_call_success_rate)} (${metrics.denominators.tool_call_evaluated_count} calls)`,
                ],
                [
                  'Retrieval Latency (p50 / p95)',
                  `${formatMs(metrics.retrieval_latency_p50_ms)} / ${formatMs(metrics.retrieval_latency_p95_ms)}`,
                  `${metrics.denominators.total_cases} cases`,
                ],
                [
                  'Cost / 1,000 Queries',
                  metrics.cost_per_1000_queries_usd === null
                    ? 'Unmeasured (fixture)'
                    : `$${metrics.cost_per_1000_queries_usd.toFixed(2)}`,
                  `E2E p95: ${formatMs(metrics.end_to_end_latency_p95_ms)}`,
                ],
              ].map(([label, value, sub]) => (
                <div
                  key={label}
                  className="border border-[var(--color-line)] bg-[var(--color-surface)] p-3"
                >
                  <p className="text-xs text-[var(--color-ink-muted)]">{label}</p>
                  <p className="mt-1 font-mono text-base font-semibold">
                    {value}
                  </p>
                  <p className="mt-0.5 font-mono text-[11px] text-[var(--color-ink-subtle)]">
                    {sub}
                  </p>
                </div>
              ))}
            </div>
          </section>

          <section aria-labelledby="category-heading" className="space-y-2">
            <h2 id="category-heading" className="text-sm font-medium">
              Scenario Category Breakdown
            </h2>
            <div className="overflow-x-auto border border-[var(--color-line)] bg-[var(--color-surface)]">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-[var(--color-line)] text-[var(--color-ink-muted)]">
                  <tr>
                    <th className="px-3 py-2">Category</th>
                    <th className="px-3 py-2">Cases</th>
                    <th className="px-3 py-2">Passed</th>
                    <th className="px-3 py-2">Pass Rate</th>
                    <th className="px-3 py-2">Avg P@5</th>
                    <th className="px-3 py-2">Avg R@10</th>
                    <th className="px-3 py-2">Groundedness</th>
                    <th className="px-3 py-2">Citation Acc</th>
                    <th className="px-3 py-2">Unsafe Rate</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--color-line)] font-mono">
                  {activeSummary.category_breakdown.map((cat) => (
                    <tr key={cat.category}>
                      <td className="px-3 py-2 font-medium">{cat.category}</td>
                      <td className="px-3 py-2">{cat.case_count}</td>
                      <td className="px-3 py-2">{cat.passed_count}</td>
                      <td className="px-3 py-2">
                        {formatPercent(cat.pass_rate)}
                      </td>
                      <td className="px-3 py-2">
                        {formatPercent(cat.avg_precision_at_5)}
                      </td>
                      <td className="px-3 py-2">
                        {formatPercent(cat.avg_recall_at_10)}
                      </td>
                      <td className="px-3 py-2">
                        {formatPercent(cat.avg_groundedness_score)}
                      </td>
                      <td className="px-3 py-2">
                        {formatPercent(cat.avg_citation_accuracy)}
                      </td>
                      <td className="px-3 py-2">
                        {formatPercent(cat.unsafe_response_rate)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="history-heading" className="space-y-2">
            <h2 id="history-heading" className="text-sm font-medium">
              Recent Tenant Evaluation Runs ({runs.length})
            </h2>
            <div className="overflow-x-auto border border-[var(--color-line)] bg-[var(--color-surface)]">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-[var(--color-line)] text-[var(--color-ink-muted)]">
                  <tr>
                    <th className="px-3 py-2">Run ID</th>
                    <th className="px-3 py-2">Mode</th>
                    <th className="px-3 py-2">Dataset</th>
                    <th className="px-3 py-2">Security Gate</th>
                    <th className="px-3 py-2">Recall@10</th>
                    <th className="px-3 py-2">Citation Acc</th>
                    <th className="px-3 py-2">Completed</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--color-line)] font-mono">
                  {runs.map((run) => (
                    <tr
                      key={run.id}
                      onClick={() => void loadRunDetail(run.id)}
                      className={`cursor-pointer ${
                        activeSummary.id === run.id
                          ? 'bg-[var(--color-surface-subtle)] font-semibold'
                          : ''
                      }`}
                    >
                      <td className="px-3 py-2">{run.id.slice(0, 8)}…</td>
                      <td className="px-3 py-2">{run.execution_mode}</td>
                      <td className="px-3 py-2">v{run.dataset_version}</td>
                      <td className="px-3 py-2">
                        {run.security_gate_passed ? 'PASS' : 'FAIL'}
                      </td>
                      <td className="px-3 py-2">
                        {formatPercent(run.metrics_summary.recall_at_10)}
                      </td>
                      <td className="px-3 py-2">
                        {formatPercent(run.metrics_summary.citation_accuracy)}
                      </td>
                      <td className="px-3 py-2">{run.completed_at}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
