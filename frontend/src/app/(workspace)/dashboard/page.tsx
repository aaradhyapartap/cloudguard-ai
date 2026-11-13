'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ArrowRight,
  BarChart3,
  FileText,
  Info,
  MessageSquare,
  ShieldAlert,
} from 'lucide-react';
import { useAuth } from '@/components/auth/AuthProvider';
import { api } from '@/lib/api-client';
import type {
  ComplianceAssessmentResponse,
  DocumentResponse,
  EvaluationRunListResponse,
  HealthStatus,
  Page,
  PendingApproval,
} from '@/lib/types';

type WidgetState<T> =
  | { status: 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error' }
  | { status: 'forbidden' };

const ASSESSMENT_PAGE_LIMIT = 5;
const APPROVAL_PAGE_LIMIT = 10;

function formatLimitedCount(count: number, limit: number): string {
  return count >= limit ? `${limit}+ (first page)` : String(count);
}

export default function DashboardPage() {
  const { me, can } = useAuth();
  const [readiness, setReadiness] = useState<HealthStatus | null>(null);
  const [readinessError, setReadinessError] = useState(false);

  const [docsState, setDocsState] = useState<WidgetState<number>>({ status: 'loading' });
  const [assessmentsState, setAssessmentsState] = useState<
    WidgetState<ComplianceAssessmentResponse[]>
  >({ status: 'loading' });
  const [approvalsState, setApprovalsState] = useState<WidgetState<PendingApproval[]>>({
    status: 'loading',
  });
  const [evalsState, setEvalsState] = useState<WidgetState<number>>({ status: 'loading' });

  const canReadDocs = can('document:read');
  const canQueryAi = can('document:read') && can('ai:query');
  const canReadRisks = can('risk:read');
  const canReadApprovals = can('approval:read');
  const canReadAnalytics = can('analytics:read_own') || can('analytics:read_team');

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setReadinessError(false);
      setDocsState(canReadDocs ? { status: 'loading' } : { status: 'forbidden' });
      setAssessmentsState(
        canReadRisks ? { status: 'loading' } : { status: 'forbidden' },
      );
      setApprovalsState(
        canReadApprovals ? { status: 'loading' } : { status: 'forbidden' },
      );
      setEvalsState(
        canReadAnalytics ? { status: 'loading' } : { status: 'forbidden' },
      );

      const [healthRes, docsRes, assessmentsRes, approvalsRes, evalsRes] =
        await Promise.allSettled([
          api.get<HealthStatus>('/health/ready', { anonymous: true }),
          canReadDocs
            ? api.get<Page<DocumentResponse>>('/documents?limit=5&offset=0')
            : Promise.resolve(null),
          canReadRisks
            ? api.get<ComplianceAssessmentResponse[]>(
                `/compliance/assessments?limit=${ASSESSMENT_PAGE_LIMIT}&offset=0`,
              )
            : Promise.resolve(null),
          canReadApprovals
            ? api.get<PendingApproval[]>(`/approvals?limit=${APPROVAL_PAGE_LIMIT}`)
            : Promise.resolve(null),
          canReadAnalytics
            ? api.get<EvaluationRunListResponse>('/evaluations/runs?limit=5')
            : Promise.resolve(null),
        ]);

      if (cancelled) return;

      if (healthRes.status === 'fulfilled') {
        setReadiness(healthRes.value);
      } else {
        setReadinessError(true);
      }

      if (canReadDocs) {
        if (docsRes.status === 'fulfilled' && docsRes.value) {
          setDocsState({ status: 'ready', data: docsRes.value.total });
        } else {
          setDocsState({ status: 'error' });
        }
      }

      if (canReadRisks) {
        if (assessmentsRes.status === 'fulfilled' && assessmentsRes.value) {
          setAssessmentsState({ status: 'ready', data: assessmentsRes.value });
        } else {
          setAssessmentsState({ status: 'error' });
        }
      }

      if (canReadApprovals) {
        if (approvalsRes.status === 'fulfilled' && approvalsRes.value) {
          setApprovalsState({ status: 'ready', data: approvalsRes.value });
        } else {
          setApprovalsState({ status: 'error' });
        }
      }

      if (canReadAnalytics) {
        if (evalsRes.status === 'fulfilled' && evalsRes.value) {
          setEvalsState({ status: 'ready', data: evalsRes.value.total });
        } else {
          setEvalsState({ status: 'error' });
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [canReadDocs, canReadRisks, canReadApprovals, canReadAnalytics]);

  const isLocal = readiness?.environment === 'local';

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <header className="flex flex-col gap-2 border-b border-[var(--color-line)] pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-accent)]">
            Compliance Intelligence Overview
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-[var(--color-ink)]">
            Workspace Overview
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--color-ink-muted)]">
            Server-authoritative posture, evidence-backed compliance workflows, and
            role-scoped controls for your organization.
          </p>
        </div>

        {readiness ? (
          <div className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-xs">
            <span className="text-[var(--color-ink-muted)]">Readiness: </span>
            <span className="font-mono font-medium text-[var(--color-ok)]">
              {readiness.status}
            </span>
            <span className="mx-1.5 text-[var(--color-line-strong)]">|</span>
            <span className="font-mono text-[var(--color-ink-subtle)]">
              db: {readiness.dependencies.database ?? 'unknown'}
            </span>
          </div>
        ) : readinessError ? (
          <div className="rounded-[var(--radius-sm)] border border-red-300 bg-red-50 px-3 py-2 text-xs text-[var(--color-error)]">
            Readiness check unavailable
          </div>
        ) : null}
      </header>

      {isLocal && (
        <aside
          aria-label="Runtime environment notice"
          className="flex items-start gap-3 rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] px-4 py-3 text-sm text-[var(--color-ink-muted)]"
        >
          <Info size={16} className="mt-0.5 shrink-0 text-[var(--color-accent)]" aria-hidden />
          <div>
            <p className="font-medium text-[var(--color-ink)]">
              Local development environment active
            </p>
            <p className="mt-0.5 text-xs">
              Database state is live; depending on backend startup flags, document storage,
              event publishing, vector search, or LLM responses may use local in-memory or
              mock adapters.
            </p>
          </div>
        </aside>
      )}

      <section
        aria-label="Permitted workspace metrics"
        className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
      >
        {canReadDocs && (
          <Link
            href="/documents"
            className="group rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5 transition-colors hover:border-[var(--color-accent)]"
          >
            <div className="flex items-center justify-between text-[var(--color-ink-muted)]">
              <span className="text-xs font-medium uppercase tracking-wide">Documents</span>
              <FileText size={16} aria-hidden className="text-[var(--color-accent)]" />
            </div>
            <p className="numeric mt-3 text-2xl font-semibold text-[var(--color-ink)]">
              {docsState.status === 'loading'
                ? 'Loading…'
                : docsState.status === 'error'
                  ? 'Unavailable'
                  : docsState.status === 'ready'
                    ? docsState.data
                    : '—'}
            </p>
            <p className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] group-hover:underline">
              <span>Open library</span>
              <ArrowRight size={12} aria-hidden />
            </p>
          </Link>
        )}

        {canReadRisks && (
          <Link
            href="/risks"
            className="group rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5 transition-colors hover:border-[var(--color-accent)]"
          >
            <div className="flex items-center justify-between text-[var(--color-ink-muted)]">
              <span className="text-xs font-medium uppercase tracking-wide">
                Recent Assessments
              </span>
              <ShieldAlert size={16} aria-hidden className="text-[var(--color-accent)]" />
            </div>
            <p className="numeric mt-3 text-2xl font-semibold text-[var(--color-ink)]">
              {assessmentsState.status === 'loading'
                ? 'Loading…'
                : assessmentsState.status === 'error'
                  ? 'Unavailable'
                  : assessmentsState.status === 'ready'
                    ? formatLimitedCount(
                        assessmentsState.data.length,
                        ASSESSMENT_PAGE_LIMIT,
                      )
                    : '—'}
            </p>
            <p className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] group-hover:underline">
              <span>Inspect assessments</span>
              <ArrowRight size={12} aria-hidden />
            </p>
          </Link>
        )}

        {canReadApprovals && (
          <Link
            href="/risks"
            className="group rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5 transition-colors hover:border-[var(--color-accent)]"
          >
            <div className="flex items-center justify-between text-[var(--color-ink-muted)]">
              <span className="text-xs font-medium uppercase tracking-wide">
                Pending Approvals
              </span>
              <ShieldAlert size={16} aria-hidden className="text-[var(--color-warn)]" />
            </div>
            <p className="numeric mt-3 text-2xl font-semibold text-[var(--color-ink)]">
              {approvalsState.status === 'loading'
                ? 'Loading…'
                : approvalsState.status === 'error'
                  ? 'Unavailable'
                  : approvalsState.status === 'ready'
                    ? formatLimitedCount(
                        approvalsState.data.length,
                        APPROVAL_PAGE_LIMIT,
                      )
                    : '—'}
            </p>
            <p className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] group-hover:underline">
              <span>Review queue</span>
              <ArrowRight size={12} aria-hidden />
            </p>
          </Link>
        )}

        {canReadAnalytics && (
          <Link
            href="/analytics"
            className="group rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5 transition-colors hover:border-[var(--color-accent)]"
          >
            <div className="flex items-center justify-between text-[var(--color-ink-muted)]">
              <span className="text-xs font-medium uppercase tracking-wide">
                Evaluation Runs
              </span>
              <BarChart3 size={16} aria-hidden className="text-[var(--color-accent)]" />
            </div>
            <p className="numeric mt-3 text-2xl font-semibold text-[var(--color-ink)]">
              {evalsState.status === 'loading'
                ? 'Loading…'
                : evalsState.status === 'error'
                  ? 'Unavailable'
                  : evalsState.status === 'ready'
                    ? evalsState.data
                    : '—'}
            </p>
            <p className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] group-hover:underline">
              <span>View evaluation gates</span>
              <ArrowRight size={12} aria-hidden />
            </p>
          </Link>
        )}

        {canQueryAi && (
          <Link
            href="/assistant"
            className="group rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5 transition-colors hover:border-[var(--color-accent)]"
          >
            <div className="flex items-center justify-between text-[var(--color-ink-muted)]">
              <span className="text-xs font-medium uppercase tracking-wide">
                AI Assistant
              </span>
              <MessageSquare size={16} aria-hidden className="text-[var(--color-accent)]" />
            </div>
            <p className="mt-3 text-sm font-medium text-[var(--color-ink)]">
              Grounded RAG Q&amp;A
            </p>
            <p className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] group-hover:underline">
              <span>Ask a compliance question</span>
              <ArrowRight size={12} aria-hidden />
            </p>
          </Link>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        <section
          aria-labelledby="caller-identity-heading"
          className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5 lg:col-span-2"
        >
          <h2
            id="caller-identity-heading"
            className="text-sm font-semibold text-[var(--color-ink)]"
          >
            Caller &amp; Server-Authoritative Permissions
          </h2>
          <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">
            Resolved from <span className="font-mono">/api/v1/me</span>. Navigation and
            actions reflect these server-enforced grants.
          </p>

          {me ? (
            <div className="mt-4 space-y-4">
              <dl className="grid gap-3 border-t border-[var(--color-line)] pt-4 sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-[var(--color-ink-muted)]">Organization ID</dt>
                  <dd className="numeric mt-0.5 truncate text-xs text-[var(--color-ink)]">
                    {me.organization_id}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-[var(--color-ink-muted)]">User ID</dt>
                  <dd className="numeric mt-0.5 truncate text-xs text-[var(--color-ink)]">
                    {me.user_id}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-[var(--color-ink-muted)]">Role</dt>
                  <dd className="mt-0.5 font-mono text-xs font-medium text-[var(--color-ink)]">
                    {me.role}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-[var(--color-ink-muted)]">
                    Visible Confidentiality Ceiling
                  </dt>
                  <dd className="mt-0.5 font-mono text-xs text-[var(--color-ink)]">
                    {me.visible_confidentiality_levels.join(', ')}
                  </dd>
                </div>
              </dl>

              <div className="border-t border-[var(--color-line)] pt-4">
                <p className="mb-2 text-xs font-medium text-[var(--color-ink-muted)]">
                  Granted permissions ({me.permissions.length})
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {me.permissions.map((permission) => (
                    <li
                      key={permission}
                      className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] px-2 py-0.5 font-mono text-[11px] text-[var(--color-ink-muted)]"
                    >
                      {permission}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <p className="mt-4 text-sm text-[var(--color-ink-muted)]">No caller resolved.</p>
          )}
        </section>

        <section
          aria-labelledby="recent-assessments-heading"
          className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5"
        >
          <h2
            id="recent-assessments-heading"
            className="text-sm font-semibold text-[var(--color-ink)]"
          >
            Recent Assessments
          </h2>
          <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">
            Latest tenant compliance assessments (up to {ASSESSMENT_PAGE_LIMIT}).
          </p>

          {!canReadRisks ? (
            <p className="mt-4 text-xs text-[var(--color-ink-muted)]">
              Your role does not include <span className="font-mono">risk:read</span>.
            </p>
          ) : assessmentsState.status === 'loading' ? (
            <p className="mt-4 text-xs text-[var(--color-ink-muted)]">
              Loading assessments…
            </p>
          ) : assessmentsState.status === 'error' ? (
            <p role="alert" className="mt-4 text-xs text-[var(--color-error)]">
              Compliance assessments are temporarily unavailable.
            </p>
          ) : assessmentsState.status === 'ready' && assessmentsState.data.length === 0 ? (
            <div className="mt-4 rounded-[var(--radius-sm)] border border-dashed border-[var(--color-line-strong)] p-4 text-xs text-[var(--color-ink-muted)]">
              No compliance assessments found yet. Create or inspect assessments in the{' '}
              <Link href="/risks" className="font-medium text-[var(--color-accent)] underline">
                Risk &amp; Approvals
              </Link>{' '}
              workspace.
            </div>
          ) : assessmentsState.status === 'ready' ? (
            <ul className="mt-4 divide-y divide-[var(--color-line)]">
              {assessmentsState.data.map((item) => (
                <li key={item.id} className="py-2.5">
                  <p className="truncate text-sm font-medium text-[var(--color-ink)]">
                    {item.title}
                  </p>
                  <div className="mt-1 flex items-center justify-between font-mono text-[11px] text-[var(--color-ink-subtle)]">
                    <span>{item.status}</span>
                    <span>
                      {item.effective_overall_score ?? item.overall_score ?? 'unscored'} ·{' '}
                      {item.effective_risk_classification ?? item.risk_classification}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      </div>
    </div>
  );
}
