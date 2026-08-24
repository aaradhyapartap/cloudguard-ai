'use client';

import { LogOut } from 'lucide-react';
import { useAuth } from '@/components/auth/AuthProvider';
import { HealthBadge } from '@/components/system/HealthBadge';

/**
 * Identity strip.
 *
 * Reads the caller from the auth context, which got it from `/me` — not by
 * decoding the token client-side. The server is the only authority on who
 * someone is and what they may do; the client renders what it is told. One copy
 * of that logic is what stops the UI and the API from disagreeing.
 */
export function TopBar() {
  const { me, logout } = useAuth();

  return (
    <header className="flex items-center justify-between border-b border-[var(--color-line)] bg-[var(--color-surface)] px-6 py-3">
      <HealthBadge />

      <div className="flex items-center gap-4">
        {me && (
          <>
            <div className="text-right leading-tight">
              <p className="text-sm font-medium">{me.email}</p>
              <p className="font-mono text-[11px] text-[var(--color-ink-subtle)]">
                {me.role}
                {me.department ? ` · ${me.department}` : ''}
              </p>
            </div>
            <div
              aria-hidden
              className="flex size-8 items-center justify-center rounded-[var(--radius-md)] bg-[var(--color-surface-sunken)] font-mono text-xs font-medium"
            >
              {me.email.slice(0, 2).toUpperCase()}
            </div>
          </>
        )}

        <button
          type="button"
          onClick={logout}
          title="Sign out"
          className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--color-line)] px-2 py-1.5 text-xs text-[var(--color-ink-muted)] transition-colors hover:bg-[var(--color-surface-sunken)] hover:text-[var(--color-ink)]"
        >
          <LogOut size={13} aria-hidden />
          Sign out
        </button>
      </div>
    </header>
  );
}
