'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/components/auth/AuthProvider';
import { ApiError } from '@/lib/api-client';

/**
 * Login.
 *
 * Renders one of two forms depending on what `/auth/config` reports, rather
 * than on a build-time flag. The environment decides how people sign in, and
 * the environment is a runtime fact.
 *
 * The local roster is listed on screen on purpose: these are fixture accounts
 * in a development database with no passwords and no real mailboxes. Hiding
 * them would only mean looking them up in a file.
 */

const LOCAL_ROSTER = [
  { email: 'analyst@acme.test', role: 'Analyst', org: 'Acme' },
  { email: 'manager@acme.test', role: 'Manager', org: 'Acme' },
  { email: 'admin@acme.test', role: 'Administrator', org: 'Acme' },
  { email: 'analyst@globex.test', role: 'Analyst', org: 'Globex' },
] as const;

export default function LoginPage() {
  const { status, authConfig, loginLocal, loginHosted } = useAuth();
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
          : 'Could not reach the API. Is the backend running on port 8000?',
      );
      setPending(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <header className="mb-8">
          <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-ink-subtle)]">
            CloudGuard
          </p>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">
            Sign in to continue
          </h1>
        </header>

        {authConfig?.local_login_enabled ? (
          <section className="border border-[var(--color-line)] bg-[var(--color-surface)] p-5">
            <label
              htmlFor="email"
              className="block text-sm font-medium"
            >
              Work email
            </label>
            <select
              id="email"
              value={email}
              disabled={pending}
              onChange={(event) => setEmail(event.target.value)}
              className="mt-1.5 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-white px-2.5 py-2 font-mono text-xs disabled:opacity-50"
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
              className="mt-4 w-full rounded-[var(--radius-sm)] bg-[var(--color-rail)] px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--color-rail-hover)] disabled:opacity-50"
            >
              {pending ? 'Signing in…' : 'Sign in'}
            </button>

            {error && (
              <p
                role="alert"
                className="mt-3 border-l-[3px] border-l-[var(--color-error)] bg-[var(--color-surface-sunken)] px-3 py-2 text-sm"
              >
                {error}
              </p>
            )}

            <p className="mt-4 border-t border-[var(--color-line)] pt-3 text-xs text-[var(--color-ink-muted)]">
              Development accounts, no passwords. Signed locally rather than by
              Cognito — the token is verified by the same code either way.
            </p>
          </section>
        ) : (
          <section className="border border-[var(--color-line)] bg-[var(--color-surface)] p-5">
            <p className="text-sm text-[var(--color-ink-muted)]">
              You will be redirected to your organization&rsquo;s sign-in page.
            </p>
            <button
              type="button"
              onClick={loginHosted}
              disabled={!authConfig?.hosted_ui_domain}
              className="mt-4 w-full rounded-[var(--radius-sm)] bg-[var(--color-rail)] px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--color-rail-hover)] disabled:opacity-50"
            >
              Continue to sign-in
            </button>
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
