import { StrictMode, useEffect } from 'react';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider, useAuth } from './AuthProvider';
import { api } from '@/lib/api-client';
import {
  buildHostedAuthorizeUrl,
  getActiveToken,
  loadSession,
  setActiveToken,
} from '@/lib/auth';
import type { AuthConfig, Me } from '@/lib/types';

const replaceMock = vi.fn();
const pushMock = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    replace: replaceMock,
    push: pushMock,
  }),
}));

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function encodeBase64UrlJson(payload: Record<string, unknown>): string {
  const json = JSON.stringify(payload);
  return Buffer.from(json, 'utf-8')
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

function buildTestIdToken(overrides: Record<string, unknown> = {}): string {
  const header = encodeBase64UrlJson({ alg: 'RS256', typ: 'JWT' });
  const payload = encodeBase64UrlJson({
    iss: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Pool123',
    aud: 'client-123',
    token_use: 'id',
    nonce: 'nonce-abc',
    exp: Math.floor(Date.now() / 1000) + 3600,
    ...overrides,
  });
  return `${header}.${payload}.signature`;
}

const COGNITO_CONFIG: AuthConfig = {
  provider: 'cognito',
  client_id: 'client-123',
  hosted_ui_domain: 'cloudguard-test.auth.us-east-1.amazoncognito.com',
  issuer: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Pool123',
  scopes: ['openid', 'email', 'profile'],
  local_login_enabled: false,
};

const TEST_PROFILE: Me = {
  user_id: '00000000-0000-4000-8000-000000000001',
  organization_id: '00000000-0000-4000-8000-000000000002',
  email: 'analyst@cloudguard.ai',
  role: 'analyst',
  department: 'Security',
  permissions: ['document:read', 'ai:query'],
  visible_confidentiality_levels: ['public', 'internal', 'confidential'],
};

function seedPendingPkceTransaction(params?: {
  state?: string;
  nonce?: string;
  codeVerifier?: string;
  redirectUri?: string;
  createdAt?: number;
}) {
  const tx = {
    state: params?.state ?? 'state-xyz',
    nonce: params?.nonce ?? 'nonce-abc',
    codeVerifier: params?.codeVerifier ?? 'v'.repeat(64),
    redirectUri: params?.redirectUri ?? 'http://localhost:3000/login/',
    createdAt: params?.createdAt ?? Date.now(),
  };
  sessionStorage.setItem('cloudguard.auth.pkce', JSON.stringify(tx));
  return tx;
}

function StatusProbe({
  onState,
}: {
  onState?: (state: ReturnType<typeof useAuth>) => void;
}) {
  const state = useAuth();
  useEffect(() => {
    onState?.(state);
  }, [onState, state]);

  return (
    <div>
      <span data-testid="status">{state.status}</span>
      <span data-testid="email">{state.me?.email ?? ''}</span>
      <span data-testid="error">{state.authError ?? ''}</span>
    </div>
  );
}

describe('AuthProvider hosted login lifecycle and PKCE handling', () => {
  beforeEach(() => {
    sessionStorage.clear();
    setActiveToken(null);
    replaceMock.mockReset();
    pushMock.mockReset();
    window.history.replaceState({}, '', '/login/');
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('Strict Mode remount: first /auth/config resolves BEFORE second -> cancelled run does not steal callback, active run authenticates once', async () => {
    seedPendingPkceTransaction();
    window.history.replaceState({}, '', '/login/?code=auth-code-1&state=state-xyz');

    const configCall1 = createDeferred<AuthConfig>();
    const configCall2 = createDeferred<AuthConfig>();
    let configCallCount = 0;

    const getSpy = vi.spyOn(api, 'get').mockImplementation((path: string) => {
      if (path === '/auth/config') {
        configCallCount += 1;
        return (configCallCount === 1
          ? configCall1.promise
          : configCall2.promise) as Promise<never>;
      }
      if (path === '/me') {
        return Promise.resolve(TEST_PROFILE) as Promise<never>;
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    const idToken = buildTestIdToken();
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () =>
        new Response(JSON.stringify({ id_token: idToken }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );

    const { getByTestId } = render(
      <StrictMode>
        <AuthProvider>
          <StatusProbe />
        </AuthProvider>
      </StrictMode>,
    );

    expect(configCallCount).toBe(2);

    // Resolve the cancelled first bootstrap's /auth/config first.
    await act(async () => {
      configCall1.resolve(COGNITO_CONFIG);
      await Promise.resolve();
    });

    // Cancelled instance must NOT have scrubbed URL, consumed PKCE, or called /oauth2/token.
    expect(window.location.search).toBe('?code=auth-code-1&state=state-xyz');
    expect(sessionStorage.getItem('cloudguard.auth.pkce')).not.toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();

    // Now resolve the active second bootstrap's /auth/config.
    await act(async () => {
      configCall2.resolve(COGNITO_CONFIG);
    });

    await waitFor(() => {
      expect(getByTestId('status').textContent).toBe('authenticated');
    });

    expect(getByTestId('email').textContent).toBe('analyst@cloudguard.ai');
    expect(window.location.search).toBe('');
    expect(sessionStorage.getItem('cloudguard.auth.pkce')).toBeNull();
    expect(loadSession()?.accessToken).toBe(idToken);
    expect(getActiveToken()).toBe(idToken);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith('/dashboard');
    expect(getSpy).toHaveBeenCalledWith('/me');
  });

  it('Strict Mode remount: second /auth/config resolves BEFORE first -> active run authenticates, late first resolution is ignored', async () => {
    seedPendingPkceTransaction();
    window.history.replaceState({}, '', '/login/?code=auth-code-2&state=state-xyz');

    const configCall1 = createDeferred<AuthConfig>();
    const configCall2 = createDeferred<AuthConfig>();
    let configCallCount = 0;

    vi.spyOn(api, 'get').mockImplementation((path: string) => {
      if (path === '/auth/config') {
        configCallCount += 1;
        return (configCallCount === 1
          ? configCall1.promise
          : configCall2.promise) as Promise<never>;
      }
      if (path === '/me') {
        return Promise.resolve(TEST_PROFILE) as Promise<never>;
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    const idToken = buildTestIdToken();
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () =>
        new Response(JSON.stringify({ id_token: idToken }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );

    const { getByTestId } = render(
      <StrictMode>
        <AuthProvider>
          <StatusProbe />
        </AuthProvider>
      </StrictMode>,
    );

    expect(configCallCount).toBe(2);

    // Resolve the active second bootstrap's /auth/config first.
    await act(async () => {
      configCall2.resolve(COGNITO_CONFIG);
    });

    await waitFor(() => {
      expect(getByTestId('status').textContent).toBe('authenticated');
    });

    // Now resolve the stale first bootstrap's /auth/config late.
    await act(async () => {
      configCall1.resolve(COGNITO_CONFIG);
      await Promise.resolve();
    });

    // Must remain authenticated with a single token exchange and no error.
    expect(getByTestId('status').textContent).toBe('authenticated');
    expect(getByTestId('error').textContent).toBe('');
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(loadSession()?.accessToken).toBe(idToken);
    expect(getActiveToken()).toBe(idToken);
  });

  it('unmount/cleanup while token exchange is in flight prevents session persistence', async () => {
    seedPendingPkceTransaction();
    window.history.replaceState({}, '', '/login/?code=auth-code-3&state=state-xyz');

    vi.spyOn(api, 'get').mockImplementation((path: string) => {
      if (path === '/auth/config') {
        return Promise.resolve(COGNITO_CONFIG) as Promise<never>;
      }
      if (path === '/me') {
        return Promise.resolve(TEST_PROFILE) as Promise<never>;
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    const tokenExchangeDeferred = createDeferred<Response>();
    vi.spyOn(globalThis, 'fetch').mockReturnValue(tokenExchangeDeferred.promise);

    const { unmount } = render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    });

    // Unmount before token exchange resolves.
    unmount();

    const idToken = buildTestIdToken();
    await act(async () => {
      tokenExchangeDeferred.resolve(
        new Response(JSON.stringify({ id_token: idToken }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
      await Promise.resolve();
    });

    expect(loadSession()).toBeNull();
    expect(getActiveToken()).toBeNull();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it('failed token exchange clears session, consumes PKCE transaction once, and surfaces error', async () => {
    seedPendingPkceTransaction();
    window.history.replaceState({}, '', '/login/?code=bad-code&state=state-xyz');

    vi.spyOn(api, 'get').mockImplementation((path: string) => {
      if (path === '/auth/config') {
        return Promise.resolve(COGNITO_CONFIG) as Promise<never>;
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: 'invalid_grant' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const { getByTestId } = render(
      <AuthProvider>
        <StatusProbe />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(getByTestId('status').textContent).toBe('anonymous');
    });

    expect(getByTestId('error').textContent).toContain(
      'Authorization code exchange failed',
    );
    expect(window.location.search).toBe('');
    expect(sessionStorage.getItem('cloudguard.auth.pkce')).toBeNull();
    expect(loadSession()).toBeNull();
    expect(getActiveToken()).toBeNull();
  });

  it('logout during in-flight callback exchange or /me check prevents stale completion from restoring session', async () => {
    seedPendingPkceTransaction();
    window.history.replaceState({}, '', '/login/?code=auth-code-logout&state=state-xyz');

    const meDeferred = createDeferred<Me>();
    vi.spyOn(api, 'get').mockImplementation((path: string) => {
      if (path === '/auth/config') {
        return Promise.resolve(COGNITO_CONFIG) as Promise<never>;
      }
      if (path === '/me') {
        return meDeferred.promise as Promise<never>;
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    const idToken = buildTestIdToken();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id_token: idToken }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    let latestAuth: ReturnType<typeof useAuth> | null = null;
    const { getByTestId } = render(
      <AuthProvider>
        <StatusProbe
          onState={(state) => {
            latestAuth = state;
          }}
        />
      </AuthProvider>,
    );

    // Wait until token exchange finished and /me was called.
    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/me');
    });

    // User triggers logout while /me is still pending.
    await act(async () => {
      latestAuth?.logout();
    });

    expect(getByTestId('status').textContent).toBe('anonymous');
    expect(loadSession()).toBeNull();
    expect(getActiveToken()).toBeNull();

    // Now the stale /me response resolves.
    await act(async () => {
      meDeferred.resolve(TEST_PROFILE);
      await Promise.resolve();
    });

    expect(getByTestId('status').textContent).toBe('anonymous');
    expect(getByTestId('email').textContent).toBe('');
    expect(loadSession()).toBeNull();
    expect(getActiveToken()).toBeNull();
    expect(replaceMock).not.toHaveBeenCalledWith('/dashboard');
  });

  it('buildHostedAuthorizeUrl generates S256 PKCE challenge, state, and nonce in sessionStorage', async () => {
    const urlStr = await buildHostedAuthorizeUrl(
      COGNITO_CONFIG,
      'http://localhost:3000/login/',
    );
    const url = new URL(urlStr);
    expect(url.origin).toBe(
      'https://cloudguard-test.auth.us-east-1.amazoncognito.com',
    );
    expect(url.pathname).toBe('/oauth2/authorize');
    expect(url.searchParams.get('response_type')).toBe('code');
    expect(url.searchParams.get('code_challenge_method')).toBe('S256');
    expect(url.searchParams.get('code_challenge')).toBeTruthy();

    const stored = JSON.parse(
      sessionStorage.getItem('cloudguard.auth.pkce') ?? '{}',
    ) as { state?: string; nonce?: string; codeVerifier?: string };
    expect(stored.state).toBe(url.searchParams.get('state'));
    expect(stored.nonce).toBe(url.searchParams.get('nonce'));
    expect((stored.codeVerifier ?? '').length).toBeGreaterThanOrEqual(43);
  });
});
