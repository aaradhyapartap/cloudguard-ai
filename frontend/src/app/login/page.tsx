'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ShieldCheck } from 'lucide-react';
import { useAuth } from '@/components/auth/AuthProvider';
import { ApiError } from '@/lib/api-client';

const LOCAL_ROSTER = [
  { email: 'analyst@acme.test', role: 'Analyst', org: 'Acme' },
  { email: 'manager@acme.test', role: 'Manager', org: 'Acme' },
  { email: 'admin@acme.test', role: 'Administrator', org: 'Acme' },
  { email: 'analyst@globex.test', role: 'Analyst', org: 'Globex' },
] as const;

export default function LoginPage() {
  const { status, authConfig, authError, loginLocal, loginHosted } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState('analyst@acme.test');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (status === 'authenticated') router.replace('/dashboard');
  }, [status, router]);

  const submit = async () => {
    setPending(true);
    setError(null);
    try {
      await loginLocal(email);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not reach the API. Verify that the backend service is running.',
      );
      setPending(false);
    }
  };

  const displayError = error ?? authError;

  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--color-canvas)] px-4 py-10">
      <div className="w-full max-w-md space-y-6">
        <header className="space-y-2">
          <div className="inline-flex items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--color-accent-border)] bg-[var(--color-accent-soft)] px-2.5 py-1 text-xs font-medium text-[var(--color-accent)]">
            <ShieldCheck size={14} aria-hidden />
            <span>CloudGuard AI</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-[var(--color-ink)]">
            Compliance Intelligence Workspace
          </h1>
          <p className="text-sm text-[var(--color-ink-muted)]">
            Sign in with your organization identity to access documents, grounded AI
            analysis, deterministic risk scoring, and human-in-the-loop approvals.
          </p>
        </header>

        {authConfig?.local_login_enabled ? (
          <section
            aria-label="Local development sign-in"
            className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-6 shadow-xs"
          >
            <label
              htmlFor="email"
              className="block text-sm font-medium text-[var(--color-ink)]"
            >
              Development account
            </label>
            <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">
              Select a seeded tenant persona for local token verification.
            </p>
            <select
              id="email"
              value={email}
              disabled={pending}
              onChange={(event) => setEmail(event.target.value)}
              className="mt-2.5 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-3 py-2 font-mono text-xs text-[var(--color-ink)] disabled:opacity-50"
            >
              {LOCAL_ROSTER.map((user) => (
                <option key={user.email} value={user.email}>
                  {user.email} — {user.role}, {user.org}
                </option>
              ))}
            </select>

            <button
              type="button"
              onClick={() => void submit()}
              disabled={pending}
              className="mt-4 w-full rounded-[var(--radius-sm)] bg-[var(--color-accent)] px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
            >
              {pending ? 'Signing in…' : 'Sign in to workspace'}
            </button>

            {displayError && (
              <p
                role="alert"
                className="mt-4 border-l-[3px] border-l-[var(--color-error)] bg-[var(--color-surface-sunken)] px-3 py-2 text-sm text-[var(--color-ink)]"
              >
                {displayError}
              </p>
            )}

            <p className="mt-4 border-t border-[var(--color-line)] pt-3 text-xs leading-relaxed text-[var(--color-ink-muted)]">
              Local identity mode is active: tokens are minted locally without passwords
              and validated through the same server-side RBAC and tenant-isolation pipeline.
            </p>
          </section>
        ) : (
          <section
            aria-label="Organization sign-in"
            className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-6 shadow-xs"
          >
            <p className="text-sm text-[var(--color-ink-muted)]">
              You will be redirected to your organization&rsquo;s Cognito Hosted UI
              sign-in page using OAuth2 PKCE.
            </p>
            <button
              type="button"
              onClick={() => void loginHosted()}
              disabled={!authConfig?.hosted_ui_domain}
              className="mt-4 w-full rounded-[var(--radius-sm)] bg-[var(--color-accent)] px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
            >
              Continue to sign-in
            </button>
            {displayError && (
              <p
                role="alert"
                className="mt-4 border-l-[3px] border-l-[var(--color-error)] bg-[var(--color-surface-sunken)] px-3 py-2 text-sm text-[var(--color-ink)]"
              >
                {displayError}
              </p>
            )}
            {!authConfig && (
              <p className="mt-3 text-xs text-[var(--color-ink-subtle)]">
                Loading sign-in configuration…
              </p>
            )}
          </section>
        )}
      </div>
    </main>
  );
}
