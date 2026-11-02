'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useRouter } from 'next/navigation';
import { api, UNAUTHORIZED_EVENT } from '@/lib/api-client';
import {
  buildHostedAuthorizeUrl,
  clearHostedCallbackParams,
  clearHostedLoginTransaction,
  clearSession,
  exchangeHostedAuthorizationCode,
  isValidHostedDomain,
  loadSession,
  saveSession,
  setActiveToken,
} from '@/lib/auth';
import type { AuthConfig, Me, TokenResponse } from '@/lib/types';

type Status = 'loading' | 'authenticated' | 'anonymous';

interface AuthState {
  status: Status;
  me: Me | null;
  authConfig: AuthConfig | null;
  authError: string | null;
  /** Server-authoritative. Never computed from the role in the client. */
  can: (permission: string) => boolean;
  loginLocal: (email: string) => Promise<void>;
  loginHosted: () => Promise<void>;
  logout: () => void;
}

interface CapturedHostedCallback {
  code: string | null;
  state: string | null;
  error: string | null;
}

interface PendingCallbackExchange {
  key: string;
  promise: Promise<string>;
}

let pendingCallbackExchange: PendingCallbackExchange | null = null;

function captureHostedCallback(): CapturedHostedCallback | null {
  if (typeof window === 'undefined') return null;
  const searchParams = new URLSearchParams(window.location.search);
  const code = searchParams.get('code');
  const state = searchParams.get('state');
  const error = searchParams.get('error');
  if (!error && code === null && state === null) {
    return null;
  }
  return { code, state, error };
}

function runSingleFlightCallbackExchange(params: {
  authConfig: AuthConfig;
  code: string;
  state: string;
}): Promise<string> {
  const key = `${params.state}:${params.code}`;
  if (pendingCallbackExchange && pendingCallbackExchange.key === key) {
    return pendingCallbackExchange.promise;
  }
  const promise = exchangeHostedAuthorizationCode(params);
  const entry: PendingCallbackExchange = { key, promise };
  pendingCallbackExchange = entry;
  const clearEntry = () => {
    if (pendingCallbackExchange === entry) {
      pendingCallbackExchange = null;
    }
  };
  promise.then(clearEntry, clearEntry);
  return promise;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<Status>('loading');
  const [me, setMe] = useState<Me | null>(null);
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);

  // Stable router reference so router identity changes do not re-trigger the
  // one-time bootstrap effect after URL scrubbing.
  const routerRef = useRef(router);
  routerRef.current = router;

  // Monotonic generation counter: incremented on explicit logout or 401
  // unauthorized events so in-flight async work from a pre-logout bootstrap
  // can never mutate storage, activeToken, or UI state. Each bootstrap effect
  // run also tracks its own local `cancelled` flag for unmount/remount cleanup.
  const sessionEpochRef = useRef(0);

  const endSession = useCallback(() => {
    sessionEpochRef.current += 1;
    pendingCallbackExchange = null;
    clearSession();
    setActiveToken(null);
    setMe(null);
    setStatus('anonymous');
  }, []);

  // A 401 from anywhere ends the session. One listener, so no component has to
  // remember to handle it — and none of them can forget.
  useEffect(() => {
    const onUnauthorized = () => endSession();
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [endSession]);

  // Restore a session on load (or complete an OAuth2 PKCE callback), then
  // confirm it with the server. A token that looks valid locally may have been
  // revoked, so /me is the real check.
  useEffect(() => {
    let cancelled = false;
    const myEpoch = sessionEpochRef.current;
    const isStale = () => cancelled || sessionEpochRef.current !== myEpoch;

    // Capture callback parameters synchronously on setup before any async
    // network boundary, without scrubbing window.location yet so a synchronous
    // Strict Mode cleanup + remount still sees the callback URL on the active run.
    const capturedCallback = captureHostedCallback();

    const bootstrap = async () => {
      let cfg: AuthConfig | null = null;
      try {
        cfg = await api.get<AuthConfig>('/auth/config', { anonymous: true });
      } catch {
        // The API being unreachable is not the same as being logged out, but
        // there is nothing useful to render either way.
      }

      if (isStale()) return;
      if (cfg) setAuthConfig(cfg);

      if (capturedCallback) {
        clearHostedCallbackParams();

        if (capturedCallback.error) {
          clearHostedLoginTransaction();
          if (isStale()) return;
          setAuthError('Sign-in was cancelled or denied by the identity provider.');
          clearSession();
          setActiveToken(null);
          setMe(null);
          setStatus('anonymous');
          return;
        }

        if (!cfg || cfg.provider !== 'cognito') {
          clearHostedLoginTransaction();
          if (isStale()) return;
          setAuthError('Hosted sign-in is not active in this environment.');
          clearSession();
          setActiveToken(null);
          setMe(null);
          setStatus('anonymous');
          return;
        }

        try {
          const idToken = await runSingleFlightCallbackExchange({
            authConfig: cfg,
            code: capturedCallback.code ?? '',
            state: capturedCallback.state ?? '',
          });
          if (isStale()) return;

          saveSession(idToken);
          setActiveToken(idToken);
          const profile = await api.get<Me>('/me');
          if (isStale()) return;

          setAuthError(null);
          setMe(profile);
          setStatus('authenticated');
          routerRef.current.replace('/dashboard');
          return;
        } catch (err) {
          if (isStale()) return;
          setAuthError(
            err instanceof Error
              ? err.message
              : 'Hosted sign-in verification failed. Please sign in again.',
          );
          clearSession();
          setActiveToken(null);
          setMe(null);
          setStatus('anonymous');
          return;
        }
      }

      const session = loadSession();
      if (!session) {
        if (!isStale()) setStatus('anonymous');
        return;
      }

      setActiveToken(session.accessToken);
      try {
        const profile = await api.get<Me>('/me');
        if (isStale()) return;
        setMe(profile);
        setStatus('authenticated');
      } catch {
        if (!isStale()) {
          clearSession();
          setActiveToken(null);
          setMe(null);
          setStatus('anonymous');
        }
      }
    };

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [endSession]);

  const loginLocal = useCallback(
    async (email: string) => {
      const myEpoch = sessionEpochRef.current + 1;
      sessionEpochRef.current = myEpoch;
      setAuthError(null);
      const token = await api.post<TokenResponse>(
        '/auth/dev-login',
        { email },
        { anonymous: true },
      );
      if (sessionEpochRef.current !== myEpoch) return;
      saveSession(token.access_token);
      setActiveToken(token.access_token);
      const profile = await api.get<Me>('/me');
      if (sessionEpochRef.current !== myEpoch) return;
      setMe(profile);
      setStatus('authenticated');
      router.push('/dashboard');
    },
    [router],
  );

  const loginHosted = useCallback(async () => {
    if (!authConfig) return;
    setAuthError(null);
    try {
      const redirect = `${window.location.origin}/login/`;
      const authorizeUrl = await buildHostedAuthorizeUrl(authConfig, redirect);
      window.location.href = authorizeUrl;
    } catch (err) {
      setAuthError(
        err instanceof Error
          ? err.message
          : 'Could not start hosted sign-in.',
      );
    }
  }, [authConfig]);

  const logout = useCallback(() => {
    // Bearer tokens are stateless: the server cannot revoke one it already
    // signed. Logout is the client discarding its tokens, plus — for Cognito —
    // a redirect that clears the pool's own session cookie. Without that
    // redirect the Hosted UI would silently sign the user straight back in.
    const hosted = authConfig?.hosted_ui_domain;
    const clientId = authConfig?.client_id;
    endSession();

    if (isValidHostedDomain(hosted) && clientId) {
      const params = new URLSearchParams({
        client_id: clientId,
        logout_uri: `${window.location.origin}/login/`,
      });
      window.location.href = `https://${hosted}/logout?${params}`;
      return;
    }
    router.push('/login');
  }, [authConfig, endSession, router]);

  const can = useCallback(
    (permission: string) => me?.permissions.includes(permission) ?? false,
    [me],
  );

  const value = useMemo<AuthState>(
    () => ({
      status,
      me,
      authConfig,
      authError,
      can,
      loginLocal,
      loginHosted,
      logout,
    }),
    [status, me, authConfig, authError, can, loginLocal, loginHosted, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside AuthProvider');
  return context;
}
