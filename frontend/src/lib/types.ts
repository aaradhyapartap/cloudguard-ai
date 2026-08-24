/**
 * API contract types.
 *
 * Hand-written in Phase 1 because there are three of them. Once the surface
 * grows past a handful, generate these from the backend's OpenAPI schema
 * (`openapi-typescript`) rather than maintaining two sources of truth that
 * drift — a mismatched frontend type is a runtime error the compiler
 * cheerfully approved.
 */

export type Role = 'analyst' | 'manager' | 'admin';

export interface HealthStatus {
  status: string;
  environment: string;
  version: string;
  checked_at: string;
  dependencies: Record<string, string>;
}

export interface Me {
  user_id: string;
  organization_id: string;
  email: string;
  role: Role;
  department: string | null;
  permissions: string[];
  visible_confidentiality_levels: string[];
}

export interface AuthConfig {
  provider: 'local' | 'cognito';
  issuer: string;
  hosted_ui_domain: string | null;
  client_id: string | null;
  scopes: string[];
  local_login_enabled: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown> | null;
    request_id?: string | null;
  };
}
