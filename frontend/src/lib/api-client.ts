/**
 * Typed fetch wrapper.
 *
 * Every call goes through here so correlation, auth and error shape are handled
 * once. Two behaviours worth knowing about:
 *
 * 1. The bearer token is read from the auth module at call time rather than
 *    captured at import time, so a token refresh takes effect immediately
 *    without re-creating the client.
 *
 * 2. A 401 fires an `unauthorized` event. The auth provider listens and clears
 *    the session. This decouples "the server rejected us" from "the UI must log
 *    out", which means every component gets the behaviour without any of them
 *    implementing it.
 */

import { apiUrl } from './config';
import { getActiveToken } from './auth';
import type { ApiErrorBody } from './types';

export const UNAUTHORIZED_EVENT = 'cloudguard:unauthorized';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string | null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

interface RequestOptions extends RequestInit {
  /** Skip the Authorization header — for endpoints reachable before login. */
  anonymous?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { anonymous, headers, ...init } = options;
  const token = anonymous ? null : getActiveToken();

  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      'content-type': 'application/json',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(headers ?? {}),
    },
  });

  if (!response.ok) {
    if (response.status === 401 && !anonymous) {
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
    }

    let code = 'UNKNOWN';
    let message = `Request failed with status ${response.status}`;
    try {
      const body = (await response.json()) as ApiErrorBody;
      code = body.error?.code ?? code;
      message = body.error?.message ?? message;
    } catch {
      // A non-JSON error body (a gateway timeout page, say) is not itself worth
      // surfacing — the status code already says what happened.
    }
    throw new ApiError(response.status, code, message, response.headers.get('x-request-id'));
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'POST', body: JSON.stringify(body) }),
};
