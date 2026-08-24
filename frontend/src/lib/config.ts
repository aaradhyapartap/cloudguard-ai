/**
 * Client configuration.
 *
 * Next.js inlines NEXT_PUBLIC_* at build time, so every value here is visible
 * to anyone who opens devtools. That is fine for a base URL and fatal for a
 * secret, so nothing secret is read here. The API is the only place privileged
 * operations happen, and it re-authorises every request.
 *
 * Login configuration (issuer, client id, hosted UI domain) is deliberately
 * *not* baked in at build time — it is fetched from `/auth/config` at runtime,
 * so rotating a Cognito user pool is a backend redeploy rather than a frontend
 * rebuild.
 */

export const config = {
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000',
  apiPrefix: '/api/v1',
} as const;

export const apiUrl = (path: string): string =>
  `${config.apiBaseUrl}${config.apiPrefix}${path}`;
