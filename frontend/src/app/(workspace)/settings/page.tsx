'use client';

import { useEffect, useState } from 'react';
import { Info, Server } from 'lucide-react';
import { useAuth } from '@/components/auth/AuthProvider';
import { ApiError, api } from '@/lib/api-client';
import type { HealthStatus, SystemConfigResponse } from '@/lib/types';

export default function SettingsPage() {
  const { can } = useAuth();
  const canManageSettings = can('settings:manage');

  const [sysConfig, setSysConfig] = useState<SystemConfigResponse | null>(null);
  const [readiness, setReadiness] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!canManageSettings) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [cfg, health] = await Promise.all([
          api.get<SystemConfigResponse>('/system/config'),
          api.get<HealthStatus>('/health/ready', { anonymous: true }),
        ]);
        if (!cancelled) {
          setSysConfig(cfg);
          setReadiness(health);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err.message
              : 'Unable to load runtime system configuration.',
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [canManageSettings]);

  if (!canManageSettings) {
    return (
      <div className="mx-auto max-w-4xl">
        <div className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-6">
          <h1 className="text-lg font-semibold text-[var(--color-ink)]">Settings</h1>
          <p className="mt-2 text-sm text-[var(--color-ink-muted)]">
            Runtime system configuration requires{' '}
            <span className="font-mono">settings:manage</span> (Administrator only).
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="border-b border-[var(--color-line)] pb-5">
        <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-accent)]">
          Platform Administration
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-[var(--color-ink)]">
          Runtime Adapters &amp; Feature Flags
        </h1>
        <p className="mt-1 text-sm text-[var(--color-ink-muted)]">
          Non-secret backend adapter selections and feature flags reported by{' '}
          <span className="font-mono">/api/v1/system/config</span>.
        </p>
      </header>

      {error && (
        <div
          role="alert"
          className="border-l-[3px] border-l-[var(--color-error)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-ink)]"
        >
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-[var(--color-ink-muted)]">
          Loading system configuration…
        </p>
      ) : (
        sysConfig && (
          <div className="grid gap-6 md:grid-cols-2">
            <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-ink)]">
                <Server size={15} aria-hidden className="text-[var(--color-accent)]" />
                <span>Active Providers</span>
              </h2>
              <dl className="mt-4 divide-y divide-[var(--color-line)] text-xs">
                {[
                  ['Environment', sysConfig.environment],
                  ['LLM provider', sysConfig.llm_provider],
                  ['Chat model', sysConfig.chat_model],
                  ['Embedding model', sysConfig.embedding_model],
                  ['Vector store', sysConfig.vector_store],
                  ['Document store', sysConfig.document_store],
                  ['Event publisher', sysConfig.event_publisher],
                  [
                    'Database readiness',
                    readiness?.dependencies.database ?? 'unknown',
                  ],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4 py-2.5">
                    <dt className="text-[var(--color-ink-muted)]">{label}</dt>
                    <dd className="font-mono font-medium text-[var(--color-ink)]">
                      {value}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>

            <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5">
              <h2 className="text-sm font-semibold text-[var(--color-ink)]">
                Feature Flags
              </h2>
              <ul className="mt-4 divide-y divide-[var(--color-line)] text-xs">
                {Object.entries(sysConfig.feature_flags).map(([flag, enabled]) => (
                  <li key={flag} className="flex items-center justify-between py-2.5">
                    <span className="font-mono text-[var(--color-ink)]">{flag}</span>
                    <span
                      className={`rounded-[var(--radius-sm)] border px-2 py-0.5 font-mono text-[11px] ${
                        enabled
                          ? 'border-[var(--color-accent-border)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                          : 'border-[var(--color-line)] bg-[var(--color-surface-sunken)] text-[var(--color-ink-muted)]'
                      }`}
                    >
                      {enabled ? 'enabled' : 'disabled'}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )
      )}

      <aside className="flex items-start gap-3 rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] px-4 py-3 text-xs text-[var(--color-ink-muted)]">
        <Info size={15} className="mt-0.5 shrink-0 text-[var(--color-accent)]" aria-hidden />
        <p>
          Dedicated user-directory management and audit-log query endpoints are not
          exposed in this backend API build, so those controls are intentionally omitted.
        </p>
      </aside>
    </div>
  );
}
