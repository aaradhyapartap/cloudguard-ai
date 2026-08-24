'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api-client';
import type { HealthStatus } from '@/lib/types';

type State = 'checking' | 'ok' | 'degraded' | 'unreachable';

const LABEL: Record<State, string> = {
  checking: 'Checking API',
  ok: 'API connected',
  degraded: 'API degraded',
  unreachable: 'API unreachable',
};

const DOT: Record<State, string> = {
  checking: 'bg-[var(--color-ink-subtle)]',
  ok: 'bg-[var(--color-ok)]',
  degraded: 'bg-[var(--color-warn)]',
  unreachable: 'bg-[var(--color-error)]',
};

/**
 * Live proof that the frontend and backend are actually talking.
 *
 * Worth building in Phase 1 rather than assuming: "is it my code or is the
 * server down?" is the question that eats the most time in early development,
 * and this answers it at a glance.
 */
export function HealthBadge() {
  const [state, setState] = useState<State>('checking');
  const [environment, setEnvironment] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      try {
        const health = await api.get<HealthStatus>('/health');
        if (cancelled) return;
        setState(health.status === 'ok' ? 'ok' : 'degraded');
        setEnvironment(health.environment);
      } catch {
        if (!cancelled) setState('unreachable');
      }
    };

    void check();
    const timer = setInterval(() => void check(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="flex items-center gap-2">
      <span
        aria-hidden
        className={`size-1.5 rounded-full ${DOT[state]}`}
      />
      <span className="text-xs text-[var(--color-ink-muted)]">
        {LABEL[state]}
      </span>
      {environment && (
        <span className="rounded-[var(--radius-sm)] bg-[var(--color-surface-sunken)] px-1.5 py-0.5 font-mono text-[10px] uppercase text-[var(--color-ink-muted)]">
          {environment}
        </span>
      )}
    </div>
  );
}
