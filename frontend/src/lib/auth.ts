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

const STORAGE_KEY = 'cloudguard.session';

/** Refresh this many seconds before expiry, so a request never races the clock. */
const EXPIRY_MARGIN_SECONDS = 60;

export interface Session {
  accessToken: string;
  /** Unix seconds. */
  expiresAt: number;
}

function decodeExpiry(token: string): number {
  // Reading `exp` to schedule a refresh is not verification, and nothing is
  // trusted on the basis of it. The server verifies the signature on every
  // request; this is only so the UI can log out before a call fails.
  try {
    const payload = token.split('.')[1];
    if (!payload) return 0;
    const decoded = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
    return typeof decoded.exp === 'number' ? decoded.exp : 0;
  } catch {
    return 0;
  }
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
    const session = JSON.parse(raw) as Session;
    return isExpired(session) ? null : session;
  } catch {
    return null;
  }
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
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
