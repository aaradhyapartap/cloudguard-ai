import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DashboardPage from './page';

const mockUseAuth = vi.fn();
const mockGet = vi.fn();

vi.mock('@/components/auth/AuthProvider', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('@/lib/api-client', () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

describe('DashboardPage', () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('distinguishes zero counts from unavailable error states and labels truncated lists honestly', async () => {
    const perms = new Set([
      'document:read',
      'risk:read',
      'approval:read',
      'analytics:read_team',
    ]);
    mockUseAuth.mockReturnValue({
      me: {
        user_id: 'u1',
        organization_id: 'o1',
        email: 'manager@acme.test',
        role: 'manager',
        department: 'Audit',
        permissions: Array.from(perms),
        visible_confidentiality_levels: ['public', 'internal', 'confidential'],
      },
      can: (p: string) => perms.has(p),
    });

    mockGet.mockImplementation((path: string) => {
      if (path === '/health/ready') {
        return Promise.resolve({
          status: 'ok',
          environment: 'local',
          version: '0.1.0',
          checked_at: '2026-11-12T00:00:00Z',
          dependencies: { database: 'ok' },
        });
      }
      if (path.startsWith('/documents')) {
        return Promise.resolve({ items: [], total: 0, limit: 5, offset: 0 });
      }
      if (path.startsWith('/compliance/assessments')) {
        return Promise.resolve(
          Array.from({ length: 5 }, (_, idx) => ({
            id: `a-${idx}`,
            organization_id: 'o1',
            framework_id: 'f1',
            title: `Assessment ${idx}`,
            status: 'in_progress',
            overall_score: null,
            risk_classification: 'not_scored',
            scoring_version: null,
            created_by: 'u1',
            created_at: '2026-11-12T00:00:00Z',
            updated_at: '2026-11-12T00:00:00Z',
          })),
        );
      }
      if (path.startsWith('/approvals')) {
        return Promise.reject(new Error('Approvals service down'));
      }
      if (path.startsWith('/evaluations/runs')) {
        return Promise.resolve({ items: [], total: 2, visibility_scope: 'team' });
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`));
    });

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText('0')).toBeTruthy();
      expect(screen.getByText('5+ (first page)')).toBeTruthy();
      expect(screen.getByText('Unavailable')).toBeTruthy();
      expect(screen.getByText('2')).toBeTruthy();
    });
  });

  it('shows unavailable notice when assessments endpoint fails', async () => {
    const perms = new Set(['risk:read']);
    mockUseAuth.mockReturnValue({
      me: null,
      can: (p: string) => perms.has(p),
    });

    mockGet.mockRejectedValue(new Error('Backend unavailable'));

    render(<DashboardPage />);

    await waitFor(() => {
      expect(
        screen.getByText('Compliance assessments are temporarily unavailable.'),
      ).toBeTruthy();
      expect(screen.getByText('Readiness check unavailable')).toBeTruthy();
    });
  });
});
