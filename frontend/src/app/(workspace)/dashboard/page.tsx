'use client';

import { useAuth } from '@/components/auth/AuthProvider';

/**
 * Phase 1 dashboard.
 *
 * There is no risk data yet, and inventing some would be dishonest — a
 * dashboard full of fake numbers is how a portfolio project starts lying to
 * itself. Instead this page shows what Phase 1 actually delivered: the caller
 * the server resolved, the permissions the server granted, and what is wired.
 *
 * It also establishes the severity spine — the coloured left rule that carries
 * meaning across every record view from Phase 5 onward.
 */

const SEVERITY = [
  { level: 'Critical', token: 'var(--color-severity-critical)' },
  { level: 'High', token: 'var(--color-severity-high)' },
  { level: 'Medium', token: 'var(--color-severity-medium)' },
  { level: 'Low', token: 'var(--color-severity-low)' },
] as const;

const WIRED = [
  { component: 'FastAPI application', detail: 'api → services → repositories', done: true },
  { component: 'Configuration', detail: 'typed, validated at startup', done: true },
  { component: 'Structured logging', detail: 'JSON, correlated, redacted', done: true },
  { component: 'Ports and adapters', detail: '4 ports, in-memory adapters', done: true },
  { component: 'Tenant isolation', detail: 'repository scope + Postgres RLS', done: true },
  { component: 'Authorization', detail: 'server-side matrix, 3 roles', done: true },
  { component: 'Authentication', detail: 'JWT bearer, Cognito-ready', done: true },
  { component: 'Session management', detail: 'restore, expire, sign out', done: true },
  { component: 'Document ingestion', detail: 'S3 + Step Functions — Phase 3', done: false },
  { component: 'Retrieval and generation', detail: 'Bedrock — Phase 4', done: false },
];

export default function DashboardPage() {
  const { me } = useAuth();

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-ink-subtle)]">
          Foundation
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          Phase 2 is running
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-[var(--color-ink-muted)]">
          Authenticated end to end. Risk and document views populate as later
          phases land.
        </p>
      </header>

      <section aria-labelledby="caller">
        <h2 id="caller" className="mb-2 text-sm font-medium">
          Caller, as resolved by the server
        </h2>
        <div className="border border-[var(--color-line)] bg-[var(--color-surface)]">
          {me ? (
            <dl className="divide-y divide-[var(--color-line)]">
              {[
                ['Organization', me.organization_id],
                ['User', me.user_id],
                ['Role', me.role],
                ['Clearance', me.visible_confidentiality_levels.join(', ')],
              ].map(([label, value]) => (
                <div key={label} className="flex gap-4 px-4 py-2.5 text-sm">
                  <dt className="w-32 shrink-0 text-[var(--color-ink-muted)]">
                    {label}
                  </dt>
                  <dd className="numeric min-w-0 truncate text-xs">{value}</dd>
                </div>
              ))}
              <div className="px-4 py-3">
                <p className="mb-2 text-sm text-[var(--color-ink-muted)]">
                  Permissions granted
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {me.permissions.map((permission) => (
                    <li
                      key={permission}
                      className="rounded-[var(--radius-sm)] border border-[var(--color-line)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--color-ink-muted)]"
                    >
                      {permission}
                    </li>
                  ))}
                </ul>
              </div>
            </dl>
          ) : (
            <p className="px-4 py-6 text-sm text-[var(--color-ink-muted)]">
              No caller resolved.
            </p>
          )}
        </div>
      </section>

      <section aria-labelledby="wiring">
        <h2 id="wiring" className="mb-2 text-sm font-medium">
          What is wired
        </h2>
        <ul className="border border-[var(--color-line)] bg-[var(--color-surface)] divide-y divide-[var(--color-line)]">
          {WIRED.map(({ component, detail, done }) => (
            <li key={component} className="flex items-center gap-3 px-4 py-2.5">
              <span
                aria-hidden
                className={`size-1.5 rounded-full ${
                  done ? 'bg-[var(--color-ok)]' : 'bg-[var(--color-line-strong)]'
                }`}
              />
              <span
                className={`text-sm ${done ? '' : 'text-[var(--color-ink-subtle)]'}`}
              >
                {component}
              </span>
              <span className="ml-auto font-mono text-[11px] text-[var(--color-ink-subtle)]">
                {detail}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="severity">
        <h2 id="severity" className="mb-2 text-sm font-medium">
          Severity scale
        </h2>
        <p className="mb-2 text-sm text-[var(--color-ink-muted)]">
          One ordered ramp, used only for severity, everywhere it appears. The
          left rule is how a record carries its severity from Phase 5 onward.
        </p>
        <ul className="space-y-px">
          {SEVERITY.map(({ level, token }) => (
            <li
              key={level}
              style={{ borderLeftColor: token }}
              className="border border-[var(--color-line)] border-l-[3px] bg-[var(--color-surface)] px-4 py-2.5 text-sm"
            >
              {level}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
