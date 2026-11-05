import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AnalyticsPage from './page';

const mockUseAuth = vi.fn();
const mockGet = vi.fn();
const mockPost = vi.fn();

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
  },
}));

const SAMPLE_METADATA = {
  dataset_version: '1.0.0',
  golden_case_count: 50,
  adversarial_case_count: 8,
  category_counts: {
    answerable: 12,
    unanswerable: 8,
    ambiguous: 8,
    cross_document: 8,
    unauthorized: 6,
    prompt_injection: 8,
  },
  metric_definitions: {
    precision_at_5: 'Mean precision@5',
  },
  known_limitations: ['Fixture run uses DeterministicFeatureEmbeddingProvider.'],
};

const SAMPLE_RUN = {
  id: '99999999-9999-4999-8999-999999999999',
  organization_id: '11111111-1111-4111-8111-111111111111',
  triggered_by: '44444444-4444-4444-8444-444444444444',
  dataset_version: '1.0.0',
  execution_mode: 'deterministic_fixture',
  embedding_model_id: 'eval:feature-hash-1024-v1',
  generation_model_id: 'eval:fixture-llm-v1',
  reviewer_model_id: 'eval:fixture-judge-v1',
  judge_model_id: 'eval:fixture-judge-v1',
  security_gate_passed: true,
  quality_gate_advisory_passed: true,
  metrics_summary: {
    precision_at_5: 0.7064,
    recall_at_10: 0.9706,
    context_relevance_score: 0.7201,
    groundedness_score: 1.0,
    answer_relevance_score: 0.9633,
    citation_accuracy: 0.9706,
    hallucination_rate: 0.0,
    unsafe_response_rate: 0.0,
    unanswerable_abstention_rate: 1.0,
    cross_tenant_leak_count: 0,
    unauthorized_disclosure_count: 0,
    prompt_injection_breach_count: 0,
    adversarial_corpus_pass_rate: 1.0,
    tool_call_success_rate: 1.0,
    agent_task_completion_rate: 1.0,
    retrieval_latency_p50_ms: 2.3,
    retrieval_latency_p95_ms: 2.7,
    end_to_end_latency_p50_ms: 5.5,
    end_to_end_latency_p95_ms: 12.3,
    cost_per_1000_queries_usd: null,
    denominators: {
      total_cases: 50,
      retrieval_evaluated_cases: 34,
      citation_evaluated_cases: 34,
      groundedness_evaluated_cases: 50,
      answer_evaluated_cases: 50,
      abstention_evaluated_cases: 8,
      unauthorized_evaluated_cases: 6,
      prompt_injection_evaluated_cases: 16,
      agent_evaluated_cases: 16,
      tool_call_evaluated_count: 16,
    },
  },
  category_breakdown: [
    {
      category: 'answerable',
      case_count: 12,
      passed_count: 12,
      pass_rate: 1.0,
      avg_precision_at_5: 0.7292,
      avg_recall_at_10: 1.0,
      avg_groundedness_score: 1.0,
      avg_answer_relevance_score: 1.0,
      avg_citation_accuracy: 1.0,
      hallucination_rate: 0.0,
      unsafe_response_rate: 0.0,
    },
  ],
  provenance_notes: [
    'Executed in mode=deterministic_fixture with embedding_model=eval:feature-hash-1024-v1.',
  ],
  case_results: [],
  started_at: '2026-11-02T16:00:00Z',
  completed_at: '2026-11-02T16:00:01Z',
  created_at: '2026-11-02T16:00:01Z',
};

describe('AnalyticsPage', () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders dataset metadata, provenance, and metrics for Manager and triggers a new run', async () => {
    const perms = new Set([
      'analytics:read_own',
      'analytics:read_team',
      'document:read',
      'ai:query',
    ]);
    mockUseAuth.mockReturnValue({
      me: {
        user_id: '44444444-4444-4444-8444-444444444444',
        organization_id: '11111111-1111-4111-8111-111111111111',
        email: 'manager@acme.test',
        role: 'manager',
        department: 'Audit',
        permissions: Array.from(perms),
        visible_confidentiality_levels: ['public', 'internal', 'confidential'],
      },
      can: (perm: string) => perms.has(perm),
    });

    mockGet.mockImplementation((path: string) => {
      if (path === '/evaluations/dataset') return Promise.resolve(SAMPLE_METADATA);
      if (path.startsWith('/evaluations/runs?'))
        return Promise.resolve({
          items: [SAMPLE_RUN],
          total: 1,
          visibility_scope: 'team',
        });
      if (path.startsWith('/evaluations/runs/'))
        return Promise.resolve(SAMPLE_RUN);
      return Promise.reject(new Error(`Unexpected path ${path}`));
    });
    mockPost.mockResolvedValue(SAMPLE_RUN);

    render(<AnalyticsPage />);

    await waitFor(() => {
      expect(screen.getByText('Evaluation & Analytics')).toBeTruthy();
      expect(screen.getByText('Unmeasured (fixture)')).toBeTruthy();
    });

    const triggerBtn = screen.getByRole('button', {
      name: 'Run Evaluation Suite',
    });
    fireEvent.click(triggerBtn);

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/evaluations/runs', {
        execution_mode: 'deterministic_fixture',
      });
    });
  });

  it('shows read-only badge for Admin without ai:query permission', async () => {
    const perms = new Set(['analytics:read_team', 'document:read']);
    mockUseAuth.mockReturnValue({
      me: {
        user_id: '55555555-5555-4555-8555-555555555555',
        organization_id: '11111111-1111-4111-8111-111111111111',
        email: 'admin@acme.test',
        role: 'admin',
        department: 'IT',
        permissions: Array.from(perms),
        visible_confidentiality_levels: [
          'public',
          'internal',
          'confidential',
          'restricted',
        ],
      },
      can: (perm: string) => perms.has(perm),
    });

    mockGet.mockImplementation((path: string) => {
      if (path === '/evaluations/dataset') return Promise.resolve(SAMPLE_METADATA);
      if (path.startsWith('/evaluations/runs?'))
        return Promise.resolve({
          items: [SAMPLE_RUN],
          total: 1,
          visibility_scope: 'team',
        });
      if (path.startsWith('/evaluations/runs/'))
        return Promise.resolve(SAMPLE_RUN);
      return Promise.reject(new Error(`Unexpected path ${path}`));
    });

    render(<AnalyticsPage />);

    await waitFor(() => {
      expect(
        screen.getByText(/Read-only \(admin: ai:query not granted\)/i),
      ).toBeTruthy();
    });
    expect(
      screen.queryByRole('button', { name: 'Run Evaluation Suite' }),
    ).toBeNull();
  });
});
