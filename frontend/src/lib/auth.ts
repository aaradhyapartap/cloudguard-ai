/**
 * Token storage and session lifecycle.
 *
 * **Where the token lives, and why it is a compromise** (ADR-0015).
 *
 * ADR-0012 committed to a static export with no server compute, so there is no
 * backend session to hold an httpOnly cookie. The token has to live in the
 * browser, and every browser location is readable by injected script. The
 * options, honestly:
 *
 *   in-memory only  — safest, but the session dies on every page refresh
 *   sessionStorage  — survives refresh, dies with the tab, XSS-readable
 *   localStorage    — survives everything, including an attacker's patience
 *
 * sessionStorage is chosen: it is the shortest-lived option that still allows a
 * page refresh, which matters because users refresh constantly. The residual
 * risk is real and documented in SECURITY.md rather than hidden — an XSS bug in
 * this application is a session-theft bug.
 *
 * Mitigations that are actually in place: short token lifetime, a restrictive
 * CSP, and no `dangerouslySetInnerHTML` anywhere. The hardening path, if
 * session security ever needs to be stronger than "as safe as our XSS posture",
 * is a token-exchange endpoint issuing an httpOnly cookie — which means giving
 * up the static export.
 */

import type { AuthConfig } from './types';

const STORAGE_KEY = 'cloudguard.session';
const PKCE_STORAGE_KEY = 'cloudguard.auth.pkce';

/** Refresh this many seconds before expiry, so a request never races the clock. */
const EXPIRY_MARGIN_SECONDS = 60;

/** Maximum lifetime of an in-flight OAuth2 PKCE redirect transaction (10 minutes). */
const PKCE_MAX_AGE_MS = 10 * 60 * 1000;

const HOSTED_DOMAIN_PATTERN = /^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$/;

export interface Session {
  accessToken: string;
  /** Unix seconds. */
  expiresAt: number;
}

export interface HostedLoginTransaction {
  state: string;
  nonce: string;
  codeVerifier: string;
  redirectUri: string;
  createdAt: number;
}

interface JwtPayload {
  exp?: unknown;
  iss?: unknown;
  aud?: unknown;
  nonce?: unknown;
  token_use?: unknown;
}

function decodeJwtPayload(token: string): JwtPayload | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3 || !parts[1]) return null;
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, '=');
    const decoded: unknown = JSON.parse(atob(padded));
    if (!decoded || typeof decoded !== 'object' || Array.isArray(decoded)) {
      return null;
    }
    return decoded as JwtPayload;
  } catch {
    return null;
  }
}

function decodeExpiry(token: string): number {
  // Reading `exp` to schedule a refresh is not verification, and nothing is
  // trusted on the basis of it. The server verifies the signature on every
  // request; this is only so the UI can log out before a call fails.
  const decoded = decodeJwtPayload(token);
  return decoded && typeof decoded.exp === 'number' ? decoded.exp : 0;
}

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function randomBase64Url(byteLength: number): string {
  if (typeof crypto === 'undefined' || typeof crypto.getRandomValues !== 'function') {
    throw new Error('Web Crypto API is required for hosted sign-in.');
  }
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

async function computeCodeChallengeS256(codeVerifier: string): Promise<string> {
  if (typeof crypto === 'undefined' || !crypto.subtle) {
    throw new Error('Web Crypto SubtleCrypto is required for PKCE S256.');
  }
  const data = new TextEncoder().encode(codeVerifier);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return base64UrlEncode(new Uint8Array(digest));
}

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

export function isValidHostedDomain(domain: string | null | undefined): domain is string {
  return typeof domain === 'string' && HOSTED_DOMAIN_PATTERN.test(domain);
}

export function clearHostedLoginTransaction(): void {
  try {
    sessionStorage.removeItem(PKCE_STORAGE_KEY);
  } catch {
    // Ignore storage removal errors.
  }
}

export function consumeHostedLoginTransaction(): HostedLoginTransaction | null {
  try {
    const raw = sessionStorage.getItem(PKCE_STORAGE_KEY);
    sessionStorage.removeItem(PKCE_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<HostedLoginTransaction>;
    if (
      typeof parsed.state !== 'string' ||
      !parsed.state ||
      typeof parsed.nonce !== 'string' ||
      !parsed.nonce ||
      typeof parsed.codeVerifier !== 'string' ||
      parsed.codeVerifier.length < 43 ||
      parsed.codeVerifier.length > 128 ||
      typeof parsed.redirectUri !== 'string' ||
      !parsed.redirectUri ||
      typeof parsed.createdAt !== 'number'
    ) {
      return null;
    }
    return {
      state: parsed.state,
      nonce: parsed.nonce,
      codeVerifier: parsed.codeVerifier,
      redirectUri: parsed.redirectUri,
      createdAt: parsed.createdAt,
    };
  } catch {
    clearHostedLoginTransaction();
    return null;
  }
}

export async function buildHostedAuthorizeUrl(
  authConfig: AuthConfig,
  redirectUri: string,
): Promise<string> {
  if (!isValidHostedDomain(authConfig.hosted_ui_domain) || !authConfig.client_id) {
    throw new Error('Hosted sign-in configuration is incomplete or invalid.');
  }

  // 48 bytes -> 64 base64url chars (RFC 7636 requires 43..128 chars).
  const codeVerifier = randomBase64Url(48);
  const state = randomBase64Url(24);
  const nonce = randomBase64Url(24);
  const codeChallenge = await computeCodeChallengeS256(codeVerifier);

  const transaction: HostedLoginTransaction = {
    state,
    nonce,
    codeVerifier,
    redirectUri,
    createdAt: Date.now(),
  };

  try {
    sessionStorage.setItem(PKCE_STORAGE_KEY, JSON.stringify(transaction));
  } catch {
    throw new Error(
      'Session storage is unavailable; cannot persist PKCE state for hosted sign-in.',
    );
  }

  const params = new URLSearchParams({
    client_id: authConfig.client_id,
    response_type: 'code',
    scope: authConfig.scopes.join(' '),
    redirect_uri: redirectUri,
    state,
    nonce,
    code_challenge: codeChallenge,
    code_challenge_method: 'S256',
  });

  return `https://${authConfig.hosted_ui_domain}/oauth2/authorize?${params.toString()}`;
}

export function clearHostedCallbackParams(): void {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  const oauthKeys = ['code', 'state', 'error', 'error_description'];
  let modified = false;
  for (const key of oauthKeys) {
    if (url.searchParams.has(key)) {
      url.searchParams.delete(key);
      modified = true;
    }
  }
  if (modified) {
    const cleanSearch = url.searchParams.toString();
    const cleanUrl = `${url.pathname}${cleanSearch ? `?${cleanSearch}` : ''}${url.hash}`;
    window.history.replaceState({}, document.title, cleanUrl);
  }
}

export function validateHostedIdTokenClaims(
  idToken: string,
  expected: {
    issuer: string;
    clientId: string;
    nonce: string;
  },
): void {
  const payload = decodeJwtPayload(idToken);
  if (!payload) {
    throw new Error('Hosted identity provider returned a malformed ID token.');
  }
  if (payload.token_use !== 'id') {
    throw new Error('Hosted identity provider did not return an ID token.');
  }
  if (typeof payload.iss !== 'string' || payload.iss !== expected.issuer) {
    throw new Error('ID token issuer did not match the configured identity provider.');
  }
  if (typeof payload.aud !== 'string' || payload.aud !== expected.clientId) {
    throw new Error('ID token audience did not match the configured client.');
  }
  if (
    typeof payload.nonce !== 'string' ||
    !constantTimeEqual(payload.nonce, expected.nonce)
  ) {
    throw new Error('ID token nonce did not match the pending login transaction.');
  }
  if (
    typeof payload.exp !== 'number' ||
    payload.exp - EXPIRY_MARGIN_SECONDS <= Date.now() / 1000
  ) {
    throw new Error('ID token is already expired.');
  }
}

export async function exchangeHostedAuthorizationCode(params: {
  authConfig: AuthConfig;
  code: string;
  state: string;
}): Promise<string> {
  const { authConfig, code, state } = params;
  const transaction = consumeHostedLoginTransaction();
  if (!transaction) {
    throw new Error('Sign-in session state was missing or already used. Please sign in again.');
  }

  if (Date.now() - transaction.createdAt > PKCE_MAX_AGE_MS) {
    throw new Error('Sign-in session timed out. Please sign in again.');
  }

  if (!state || !constantTimeEqual(state, transaction.state)) {
    throw new Error('Sign-in state validation failed. Please sign in again.');
  }

  if (!code) {
    throw new Error('Authorization code was missing from the callback.');
  }

  if (!isValidHostedDomain(authConfig.hosted_ui_domain) || !authConfig.client_id) {
    throw new Error('Hosted sign-in configuration is incomplete or invalid.');
  }

  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: authConfig.client_id,
    code,
    redirect_uri: transaction.redirectUri,
    code_verifier: transaction.codeVerifier,
  });

  const response = await fetch(
    `https://${authConfig.hosted_ui_domain}/oauth2/token`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: body.toString(),
    },
  );

  if (!response.ok) {
    throw new Error('Authorization code exchange failed. Please sign in again.');
  }

  const tokenPayload: unknown = await response.json();
  if (!tokenPayload || typeof tokenPayload !== 'object') {
    throw new Error('Hosted token endpoint returned an invalid response.');
  }

  const idToken = (tokenPayload as { id_token?: unknown }).id_token;
  if (typeof idToken !== 'string' || !idToken) {
    throw new Error('Hosted token response did not include an ID token.');
  }

  validateHostedIdTokenClaims(idToken, {
    issuer: authConfig.issuer,
    clientId: authConfig.client_id,
    nonce: transaction.nonce,
  });

  return idToken;
}

export function saveSession(accessToken: string): Session {
  const session: Session = { accessToken, expiresAt: decodeExpiry(accessToken) };
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Private browsing modes can refuse storage. The session still works for
    // this page view; it just will not survive a refresh.
  }
  return session;
}

export function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw) as Partial<Session>;
    if (typeof session.accessToken !== 'string' || !session.accessToken) {
      clearSession();
      return null;
    }
    const normalized: Session = {
      accessToken: session.accessToken,
      expiresAt: typeof session.expiresAt === 'number' ? session.expiresAt : 0,
    };
    if (isExpired(normalized)) {
      clearSession();
      return null;
    }
    return normalized;
  } catch {
    clearSession();
    return null;
  }
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(PKCE_STORAGE_KEY);
  } catch {
    // Nothing useful to do; the in-memory session is dropped either way.
  }
}

export function isExpired(session: Session): boolean {
  if (!session.expiresAt) return false; // unknown expiry: let the server decide
  return session.expiresAt - EXPIRY_MARGIN_SECONDS <= Date.now() / 1000;
}

/** Read by the API client. Set by the auth provider. */
let activeToken: string | null = null;

export function setActiveToken(token: string | null): void {
  activeToken = token;
}

export function getActiveToken(): string | null {
  return activeToken;
}
