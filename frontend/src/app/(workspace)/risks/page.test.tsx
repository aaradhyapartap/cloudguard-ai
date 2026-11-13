import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import RisksPage from './page';

const mockUseAuth = vi.fn();
const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();

vi.mock('@/components/auth/AuthProvider', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly code: string,
      message: string,
    ) {
      super(message);
    }
  },
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const SAMPLE_ASSESSMENT = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  organization_id: '11111111-1111-4111-8111-111111111111',
  framework_id: '22222222-2222-4222-8222-222222222222',
  title: 'SOC2 Q4 Review',
  status: 'in_progress',
  overall_score: '82.50',
  risk_classification: 'low',
  scoring_version: 'v1.0',
  created_by: '33333333-3333-4333-8333-333333333333',
  created_at: '2026-11-12T00:00:00Z',
  updated_at: '2026-11-12T00:00:00Z',
  controls: [],
  any_hidden_evidence: true,
};

const APPROVAL_A = {
  id: 'aaaa1111-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  organization_id: '11111111-1111-4111-8111-111111111111',
  workflow_execution_id: 'exec-a',
  recommendation_id: 'rec-a',
  proposed_action: {
    action_type: 'action_A',
    resource_type: 'control',
    resource_id: '11111111-1111-4111-8111-111111111111',
    summary: 'Summary for A',
  },
  evidence: [
    {
      chunk_id: 'cccc1111-1111-4111-8111-111111111111',
      document_id: 'dddd1111-1111-4111-8111-111111111111',
    },
  ],
  score_context: null,
  agent_trace_ids: [],
  generator_model_id: null,
  reviewer_model_id: null,
  status: 'pending',
  created_at: '2026-11-12T00:00:00Z',
};

const APPROVAL_B = {
  id: 'bbbb2222-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  organization_id: '11111111-1111-4111-8111-111111111111',
  workflow_execution_id: 'exec-b',
  recommendation_id: 'rec-b',
  proposed_action: {
    action_type: 'action_B',
    resource_type: 'policy',
    resource_id: '22222222-2222-4222-8222-222222222222',
    summary: 'Summary for B',
  },
  evidence: [
    {
      chunk_id: 'cccc2222-2222-4222-8222-222222222222',
      document_id: 'dddd2222-2222-4222-8222-222222222222',
    },
  ],
  score_context: null,
  agent_trace_ids: [],
  generator_model_id: null,
  reviewer_model_id: null,
  status: 'pending',
  created_at: '2026-11-12T00:00:00Z',
};

describe('RisksPage', () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockPatch.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('shows clearance omission notice and Manager override/finalize controls for Manager', async () => {
    const perms = new Set([
      'risk:read',
      'compliance:create',
      'risk:review',
      'risk:modify_severity',
      'approval:read',
      'approval:decide',
    ]);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });

    mockGet.mockImplementation((path: string) => {
      if (path.startsWith('/compliance/assessments?')) {
        return Promise.resolve([SAMPLE_ASSESSMENT]);
      }
      if (path === `/compliance/assessments/${SAMPLE_ASSESSMENT.id}`) {
        return Promise.resolve(SAMPLE_ASSESSMENT);
      }
      if (path.endsWith('/snapshots') || path.endsWith('/overrides')) {
        return Promise.resolve([]);
      }
      if (path.startsWith('/approvals')) {
        return Promise.resolve([]);
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`));
    });

    render(<RisksPage />);

    await waitFor(() => {
      expect(screen.getAllByText('SOC2 Q4 Review').length).toBeGreaterThan(0);
      expect(screen.getByText(/exceed your confidentiality clearance/i)).toBeTruthy();
      expect(
        screen.getByRole('button', { name: 'Finalize assessment (Manager)' }),
      ).toBeTruthy();
      expect(
        screen.getByRole('button', { name: 'Record score override' }),
      ).toBeTruthy();
    });
  });

  it('resets decision fields when switching approvals A -> B and prevents stale A fields on B modified decision', async () => {
    const perms = new Set(['approval:read', 'approval:decide']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });

    const detailA = deferred<typeof APPROVAL_A>();
    const detailB = deferred<typeof APPROVAL_B>();
    let aCalls = 0;

    mockGet.mockImplementation((path: string) => {
      if (path === '/approvals?limit=25') {
        return Promise.resolve([APPROVAL_A, APPROVAL_B]);
      }
      if (path === `/approvals/${APPROVAL_A.id}`) {
        aCalls += 1;
        return aCalls === 1 ? Promise.resolve(APPROVAL_A) : detailA.promise;
      }
      if (path === `/approvals/${APPROVAL_B.id}`) {
        return detailB.promise;
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`));
    });

    mockPost.mockResolvedValue({
      ...APPROVAL_B,
      status: 'decided',
      decision: 'modified',
      approver_id: '99999999-9999-4999-8999-999999999999',
      decided_at: '2026-11-12T00:05:00Z',
      justification: 'Adjusted B scope',
      comment: null,
      modified_action: {
        action_type: 'action_B',
        resource_type: 'policy',
        resource_id: APPROVAL_B.proposed_action.resource_id,
        summary: 'Summary for B',
      },
    });

    render(<RisksPage />);

    await waitFor(() => {
      expect(screen.getByText('Proposed Action: action_A')).toBeTruthy();
      expect(
        screen.getByText(/doc: dddd1111-1111-4111-8111-111111111111/i),
      ).toBeTruthy();
    });

    // Switch to modified on A
    fireEvent.change(screen.getByLabelText('Decision'), {
      target: { value: 'modified' },
    });

    // Click A again (slow) then B (fast) -> out-of-order stale response guard
    fireEvent.click(screen.getByRole('button', { name: /action_A/i }));
    fireEvent.click(screen.getByRole('button', { name: /action_B/i }));

    detailB.resolve(APPROVAL_B);
    await waitFor(() => {
      expect(screen.getByText('Proposed Action: action_B')).toBeTruthy();
    });

    // Stale A response arrives after B; must be ignored
    detailA.resolve(APPROVAL_A);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText('Proposed Action: action_B')).toBeTruthy();
    expect(
      screen.getByText(/doc: dddd2222-2222-4222-8222-222222222222/i),
    ).toBeTruthy();

    // Select modified on B and submit
    fireEvent.change(screen.getByLabelText('Decision'), {
      target: { value: 'modified' },
    });
    fireEvent.change(screen.getByLabelText(/Justification/i), {
      target: { value: 'Adjusted B scope' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Submit decision' }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        `/approvals/${APPROVAL_B.id}/decision`,
        expect.objectContaining({
          decision: 'modified',
          modified_action: APPROVAL_B.proposed_action,
        }),
      );
      expect(
        screen.getByText(/downstream execution has not succeeded yet/i),
      ).toBeTruthy();
    });
  });

  it('keeps Admin read-only without finalize or override buttons', async () => {
    const perms = new Set(['risk:read', 'approval:read']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });

    mockGet.mockImplementation((path: string) => {
      if (path.startsWith('/compliance/assessments?')) {
        return Promise.resolve([SAMPLE_ASSESSMENT]);
      }
      if (path === `/compliance/assessments/${SAMPLE_ASSESSMENT.id}`) {
        return Promise.resolve(SAMPLE_ASSESSMENT);
      }
      if (path.endsWith('/snapshots') || path.endsWith('/overrides')) {
        return Promise.resolve([]);
      }
      if (path.startsWith('/approvals')) {
        return Promise.resolve([]);
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`));
    });

    render(<RisksPage />);

    await waitFor(() => {
      expect(screen.getAllByText('SOC2 Q4 Review').length).toBeGreaterThan(0);
    });
    expect(
      screen.queryByRole('button', { name: 'Finalize assessment (Manager)' }),
    ).toBeNull();
    expect(screen.queryByRole('button', { name: 'Record score override' })).toBeNull();
  });

  it('clears previous approval A when B detail fails so A cannot accidentally be approved', async () => {
    const perms = new Set(['approval:read', 'approval:decide']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });

    mockGet.mockImplementation((path: string) => {
      if (path === '/approvals?limit=25') {
        return Promise.resolve([APPROVAL_A, APPROVAL_B]);
      }
      if (path === `/approvals/${APPROVAL_A.id}`) {
        return Promise.resolve(APPROVAL_A);
      }
      if (path === `/approvals/${APPROVAL_B.id}`) {
        return Promise.reject(new Error('Network timeout loading B'));
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`));
    });

    render(<RisksPage />);

    await waitFor(() => {
      expect(screen.getByText('Proposed Action: action_A')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /action_B/i }));

    await waitFor(() => {
      expect(screen.getByText('Unable to load approval detail.')).toBeTruthy();
    });

    expect(screen.queryByText('Proposed Action: action_A')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Submit decision' })).toBeNull();
    expect(
      screen.getByRole('button', { name: 'Retry loading approval' }),
    ).toBeTruthy();
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('guards pending decision submission for A against subsequent selection of B', async () => {
    const perms = new Set(['approval:read', 'approval:decide']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });

    const postA = deferred<
      typeof APPROVAL_A & {
        decision?: string;
        approver_id?: string;
        decided_at?: string;
      }
    >();

    mockGet.mockImplementation((path: string) => {
      if (path === '/approvals?limit=25') {
        return Promise.resolve([APPROVAL_A, APPROVAL_B]);
      }
      if (path === `/approvals/${APPROVAL_A.id}`) {
        return Promise.resolve(APPROVAL_A);
      }
      if (path === `/approvals/${APPROVAL_B.id}`) {
        return Promise.resolve(APPROVAL_B);
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`));
    });

    mockPost.mockReturnValueOnce(postA.promise);

    render(<RisksPage />);

    await waitFor(() => {
      expect(screen.getByText('Proposed Action: action_A')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Submit decision' }));
    expect(mockPost).toHaveBeenCalledWith(
      `/approvals/${APPROVAL_A.id}/decision`,
      expect.objectContaining({ decision: 'approved' }),
    );

    // Switch to B while A decision POST is still pending
    fireEvent.click(screen.getByRole('button', { name: /action_B/i }));

    await waitFor(() => {
      expect(screen.getByText('Proposed Action: action_B')).toBeTruthy();
    });

    // Stale A decision response arrives while B is selected; must not overwrite B UI
    postA.resolve({
      ...APPROVAL_A,
      status: 'decided',
      decision: 'approved',
      approver_id: '99999999-9999-4999-8999-999999999999',
      decided_at: '2026-11-12T00:05:00Z',
    });

    await new Promise((r) => setTimeout(r, 20));

    expect(screen.getByText('Proposed Action: action_B')).toBeTruthy();
    expect(screen.queryByText(/Decision recorded \(approved\)/i)).toBeNull();
    expect(screen.getByRole('button', { name: 'Submit decision' })).toBeTruthy();
  });
});
