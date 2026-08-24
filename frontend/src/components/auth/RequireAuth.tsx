'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from './AuthProvider';

/**
 * Redirects anonymous visitors to the login page.
 *
 * **This is not a security control.** Anyone can edit client state or call the
 * API directly; the server re-authorises every request regardless of what the
 * browser rendered. This exists so a logged-out user sees a login form instead
 * of a dashboard full of failed requests.
 *
 * Applied once in the workspace layout, so every page beneath it inherits the
 * behaviour rather than each one remembering to ask.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === 'anonymous') router.replace('/login');
  }, [status, router]);

  if (status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-[var(--color-ink-muted)]">Restoring session…</p>
      </div>
    );
  }

  if (status === 'anonymous') return null;

  return <>{children}</>;
}
