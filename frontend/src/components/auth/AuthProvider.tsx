'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { useRouter } from 'next/navigation';
import { api, UNAUTHORIZED_EVENT } from '@/lib/api-client';
import {
  clearSession,
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
  /** Server-authoritative. Never computed from the role in the client. */
  can: (permission: string) => boolean;
  loginLocal: (email: string) => Promise<void>;
  loginHosted: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<Status>('loading');
  const [me, setMe] = useState<Me | null>(null);
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);

  const endSession = useCallback(() => {
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

  // Restore a session on load, then confirm it with the server. A token that
  // looks valid locally may have been revoked, so /me is the real check.
  useEffect(() => {
    let cancelled = false;

    const bootstrap = async () => {
      try {
        const cfg = await api.get<AuthConfig>('/auth/config', { anonymous: true });
        if (!cancelled) setAuthConfig(cfg);
      } catch {
        // The API being unreachable is not the same as being logged out, but
        // there is nothing useful to render either way.
      }

      const session = loadSession();
      if (!session) {
        if (!cancelled) setStatus('anonymous');
        return;
      }

      setActiveToken(session.accessToken);
      try {
        const profile = await api.get<Me>('/me');
        if (cancelled) return;
        setMe(profile);
        setStatus('authenticated');
      } catch {
        if (!cancelled) endSession();
      }
    };

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [endSession]);

  const loginLocal = useCallback(
    async (email: string) => {
      const token = await api.post<TokenResponse>(
        '/auth/dev-login',
        { email },
        { anonymous: true },
      );
      saveSession(token.access_token);
      setActiveToken(token.access_token);
      const profile = await api.get<Me>('/me');
      setMe(profile);
      setStatus('authenticated');
      router.push('/dashboard');
    },
    [router],
  );

  const loginHosted = useCallback(() => {
    if (!authConfig?.hosted_ui_domain || !authConfig.client_id) return;
    // Authorization code flow with PKCE is the Phase 8 hardening. Implicit-style
    // redirect is deliberately not used; this builds the authorize URL and lets
    // the Hosted UI drive, with the callback handled in a later phase.
    const redirect = `${window.location.origin}/login/`;
    const params = new URLSearchParams({
      client_id: authConfig.client_id,
      response_type: 'code',
      scope: authConfig.scopes.join(' '),
      redirect_uri: redirect,
    });
    window.location.href = `https://${authConfig.hosted_ui_domain}/login?${params}`;
  }, [authConfig]);

  const logout = useCallback(() => {
    // Bearer tokens are stateless: the server cannot revoke one it already
    // signed. Logout is the client discarding its tokens, plus — for Cognito —
    // a redirect that clears the pool's own session cookie. Without that
    // redirect the Hosted UI would silently sign the user straight back in.
    const hosted = authConfig?.hosted_ui_domain;
    const clientId = authConfig?.client_id;
    endSession();

    if (hosted && clientId) {
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
    () => ({ status, me, authConfig, can, loginLocal, loginHosted, logout }),
    [status, me, authConfig, can, loginLocal, loginHosted, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside AuthProvider');
  return context;
}
